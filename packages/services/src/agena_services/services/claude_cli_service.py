"""Claude CLI service — runs Claude Code via CLI bridge or local binary."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Awaitable, Callable


LogCallback = Callable[[str], Awaitable[None]]


def _read_timeout_env(default: int = 3600) -> int:
    raw = (os.getenv('AGENA_CLI_TIMEOUT_SEC') or '').strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


class ClaudeCLIService:
    # 60 min default. Long CRUD-style tasks (state machine + form +
    # menu + notifications across many files) routinely need more than
    # 30 min — task 92 burned 20+ min in research alone before writing a
    # line. Override via AGENA_CLI_TIMEOUT_SEC for shorter caps.
    EXEC_TIMEOUT_SEC = _read_timeout_env(3600)

    # ── Worktree helpers ─────────────────────────────────────────────────
    @staticmethod
    def _auth_remote_url(remote_url: str | None, remote_pat: str | None) -> str:
        """Embed a PAT into an https remote URL for non-interactive fetch.

        Returns 'origin' when no URL is given, or the URL unchanged when no
        PAT is given (so SSH/credential-helper setups still work).
        """
        if not remote_url:
            return 'origin'
        if not remote_pat:
            return remote_url
        from urllib.parse import quote, urlparse, urlunparse
        p = urlparse(remote_url)
        if p.scheme not in {'http', 'https'} or not p.netloc:
            return remote_url
        host = p.hostname or p.netloc
        if p.port:
            host = f'{host}:{p.port}'
        netloc = f'{quote(p.username or "pat", safe="")}:{quote(remote_pat, safe="")}@{host}'
        return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))

    @staticmethod
    def _create_worktree(
        repo_path: str,
        task_id: str = '',
        *,
        base_ref: str | None = None,
        remote_url: str | None = None,
        remote_pat: str | None = None,
    ) -> str | None:
        """Create an isolated git worktree based on the LATEST remote base so
        each task starts from current HEAD — the industry-standard ephemeral
        workspace (Copilot/OpenHands clone fresh per task). It fetches the
        base from the authenticated remote, then adds a DETACHED worktree off
        the freshly-fetched commit. Detached avoids colliding with the base
        branch already checked out in the main repo (the old code did
        `worktree add <wt> master`, which always failed when master was
        checked out, so the agent silently fell back to a stale shared repo).

        All failures degrade safely: fresh fetch → existing tracking ref →
        local base → None (caller then runs in the main repo, the previous
        behaviour) so this can never make a working run worse.

        base_ref:
          - None   → fresh run, base = origin/HEAD (master/main), fetched fresh
          - <name> → revision run, base = that existing feature branch
        """
        repo = Path(repo_path).expanduser().resolve()
        if not (repo / '.git').exists():
            return None
        wt_name = f'.worktree-agena-{task_id or uuid.uuid4().hex[:8]}'
        wt_path = repo.parent / wt_name
        env = {
            **__import__('os').environ,
            'GIT_TERMINAL_PROMPT': '0',
            'GIT_CONFIG_COUNT': '1',
            'GIT_CONFIG_KEY_0': 'safe.directory',
            'GIT_CONFIG_VALUE_0': str(repo),
        }
        # Never reuse a worktree from a previous run — it would pin the agent
        # to yesterday's base. Always recreate clean.
        if wt_path.exists():
            try:
                subprocess.run(['git', 'worktree', 'remove', '--force', str(wt_path)],
                               cwd=str(repo), capture_output=True, text=True, timeout=30, env=env)
            except Exception:
                pass
            try:
                import shutil as _sh
                if wt_path.exists():
                    _sh.rmtree(str(wt_path), ignore_errors=True)
            except Exception:
                pass
        # Prune stale worktree admin entries (.git/worktrees/<name>). Across
        # many runs these accumulate, and a leftover entry makes `git worktree
        # add <path>` fail on the primary ref so the worktree silently lands
        # on the wrong commit (master's bare initial commit) — the agent then
        # runs against an empty skeleton and reports the feature as missing.
        try:
            subprocess.run(['git', 'worktree', 'prune'],
                           cwd=str(repo), capture_output=True, text=True, timeout=30, env=env)
        except Exception:
            pass
        auth = ClaudeCLIService._auth_remote_url(remote_url, remote_pat)
        # Resolve the base branch: an explicit base_ref (the mapping's branch
        # on normal runs, or the feature branch on revisions) wins; otherwise
        # fall back to the remote's default branch.
        if base_ref:
            base = base_ref
        else:
            base = subprocess.run(
                ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                cwd=str(repo), capture_output=True, text=True, timeout=10, env=env,
            ).stdout.strip().replace('refs/remotes/origin/', '') or 'main'

        fetched = False
        try:
            r = subprocess.run(['git', 'fetch', auth, base],
                               cwd=str(repo), capture_output=True, text=True, timeout=60, env=env)
            fetched = (r.returncode == 0)
        except Exception:
            fetched = False

        # Prefer the freshly-fetched tip, but fall back to the existing
        # remote-tracking ref (origin/<base>) and then the local branch.
        # NEVER blindly use FETCH_HEAD when the fetch failed — a stale
        # FETCH_HEAD from an earlier (default-branch) fetch was pinning the
        # worktree to master's bare initial commit, so the agent ran against
        # an empty skeleton and hallucinated / saw the feature as "missing".
        candidates = (['FETCH_HEAD'] if fetched else []) + [f'origin/{base}', base]
        _dbg = [f'base_ref={base_ref!r} base={base!r} auth_set={bool(remote_url and remote_pat)} fetched={fetched} wt_exists_pre={wt_path.exists()}']
        for ref in candidates:
            try:
                _r = subprocess.run(['git', 'worktree', 'add', '--detach', str(wt_path), ref],
                               cwd=str(repo), capture_output=True, text=True, timeout=30, env=env)
                if _r.returncode == 0:
                    _head = subprocess.run(['git', '-C', str(wt_path), 'rev-parse', '--short', 'HEAD'],
                                           capture_output=True, text=True, timeout=10).stdout.strip()
                    _dbg.append(f'ref={ref!r} -> OK HEAD={_head}')
                    try:
                        (repo.parent / f'wt_debug_{task_id}.log').write_text('\n'.join(_dbg), encoding='utf-8')
                    except Exception:
                        pass
                    return str(wt_path)
                _dbg.append(f'ref={ref!r} -> rc={_r.returncode} err={(_r.stderr or "").strip()[:120]}')
            except Exception as _e:
                _dbg.append(f'ref={ref!r} -> EXC {str(_e)[:120]}')
        try:
            (repo.parent / f'wt_debug_{task_id}.log').write_text('\n'.join(_dbg + ['RESULT=None']), encoding='utf-8')
        except Exception:
            pass
        return None

    @staticmethod
    def _remove_worktree(repo_path: str, wt_path: str) -> None:
        """Remove a worktree after task completes."""
        try:
            subprocess.run(
                ['git', 'worktree', 'remove', '--force', wt_path],
                cwd=repo_path, capture_output=True, text=True, timeout=15,
            )
        except Exception:
            pass

    async def generate_file_markdown(
        self,
        *,
        repo_path: str,
        task_title: str,
        task_description: str,
        model: str | None = None,
        log_callback: LogCallback | None = None,
        task_id: str = '',
        base_ref: str | None = None,
        remote_url: str | None = None,
        remote_pat: str | None = None,
        candidate_files: list[str] | None = None,
    ) -> str:
        candidate_section = ''
        if candidate_files:
            bullets = '\n'.join(f'- {p}' for p in candidate_files[:10])
            candidate_section = (
                '\nCANDIDATE FILES (semantic match on this task — start by Reading these, '
                'do NOT grep the whole repo first):\n'
                f'{bullets}\n'
                'If none of these turn out to be relevant after a quick Read, then you may '
                'fall back to a narrow Grep — but stay within the 5-read HARD LIMIT below.\n'
            )

        prompt = (
            'Implement the following task in the CURRENT repository.\n\n'
            'WORKFLOW:\n'
            '1. If the task includes ATTACHMENTS / SCREENSHOTS, Read each one FIRST — they are the design spec.\n'
            '2. Skim 1–3 existing files in the repo that already solve a similar problem (the task usually names a reference module). Treat THEM as the source of truth, not the framework internals.\n'
            '3. Use the Edit or Write tools to make changes directly in the repo.\n'
            '4. Cover every Acceptance Criterion the task lists. Partial implementation is a failure.\n'
            '5. After all edits are done, output a short summary listing every file you changed.\n\n'
            'RULES:\n'
            '- Actually edit the files using tools — do NOT just output code blocks.\n'
            '- CRITICAL — PRESERVE EXISTING CONTENT: For any file that already exists, ALWAYS Read it first, then use Edit (or MultiEdit) to make targeted changes. NEVER use Write to overwrite an existing file unless the task explicitly asks you to delete and recreate it. Migration files, list-style files (Upgrade.php, schema dump, route registries, enum lists, changelogs) are APPEND-ONLY by default — add your new entry to the end, do not touch existing entries.\n'
            '- If you find yourself about to call Write on an existing file, STOP and switch to Edit. Write replaces the entire file content; Edit keeps everything around your patch intact.\n'
            '- Implement EVERY part the task asks for: schema, controllers, views, state machines, menu entries, validations, notifications — whatever the task and Acceptance Criteria require.\n'
            '- Do not invent extra unrelated work. The bar is "complete the task as specified", not "as little as possible" and not "rewrite the codebase".\n'
            '- STAY OUT OF vendor/, node_modules/, dist/, build/, .venv/, framework internals. Reading framework source is almost never necessary — if the task description or a similar existing module already shows the pattern, USE that pattern instead of grepping for how the framework resolves it under the hood. Budget at most 1 vendor read in the entire run, and only when an existing repo example does not answer the question.\n'
            '- Prefer the reference module called out in the task (e.g. "use travel_requests as the reference") over searching the whole repo.\n'
            '- HARD LIMIT — token budget: at most 5 read/grep/find/glob calls TOTAL before your first Edit/Write. If you hit that ceiling without a target file picked, stop exploring and pick the closest reference you have already seen. Token cost grows linearly with discovery turns; the ceiling is firm, not advisory.\n'
            '- After you start editing, keep reads strictly to "open the file you are about to Edit". Re-reads of already-seen files for context are wasted tokens.\n'
            '- If a file is large, read it first, then make targeted edits.\n'
            '- Do NOT try to compile, build, test, or run the code.\n'
            '- Do NOT search for compilers, runtimes, or tools (go, node, python, etc.).\n'
            '- Do NOT install packages or dependencies.\n'
            '- Do NOT run any commands other than reading/editing files (or short read-only shell commands needed to find existing patterns).\n'
            '- Stop only after every acceptance criterion is implemented and you have summarized.\n'
            '- If an IMPLEMENTATION PLAN is provided, follow it exactly — edit every file listed.\n\n'
            f'Task title: {task_title}\n'
            f'Task description:\n{task_description}\n'
            f'{candidate_section}'
        )

        # Create worktree so each task works on a clean copy. For
        # revision runs `base_ref` is the existing feature branch so
        # we land an additional commit on the same PR instead of
        # branching from main again.
        wt_path = self._create_worktree(repo_path, task_id, base_ref=base_ref, remote_url=remote_url, remote_pat=remote_pat)
        effective_path = wt_path or repo_path
        if wt_path and log_callback:
            try:
                _head = subprocess.run(
                    ['git', '-C', wt_path, 'log', '--oneline', '-1'],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()
            except Exception:
                _head = '?'
            await log_callback(f'Worktree created off base_ref={base_ref or "(remote default)"} @ {_head}')

        # Store worktree info so orchestration_service can find the right path
        self.last_worktree_path = wt_path
        self.last_effective_path = effective_path

        # Surface whether a repo guide (CLAUDE.md/AGENTS.md) is present in the
        # worktree base, so the user can confirm from the task Activity/Logs feed
        # whether their committed guide is actually picked up by this run.
        # (Claude reads CLAUDE.md natively, but it only exists in the worktree if
        # it was committed to the base branch the worktree was cut from.)
        if log_callback:
            import os as _os
            _guide = next((g for g in ('CLAUDE.md', 'agents.md', 'AGENTS.md')
                           if _os.path.isfile(_os.path.join(effective_path, g))), None)
            if _guide:
                try:
                    _sz = _os.path.getsize(_os.path.join(effective_path, _guide))
                except OSError:
                    _sz = 0
                await log_callback(f'Repo guide: {_guide} present in worktree ({_sz} chars) — Claude reads it natively')
            else:
                await log_callback('Repo guide: no CLAUDE.md/AGENTS.md in worktree — commit one to the base branch to guide the agent')

        try:
            claude_bin = shutil.which('claude')
            if claude_bin:
                return await self._run_local(claude_bin, effective_path, prompt, model, log_callback)
            return await self._run_bridge(effective_path, prompt, model, log_callback, task_id=task_id)
        finally:
            # Keep worktree alive — orchestration_service collects changes via git diff
            # then calls cleanup_worktree() after PR creation
            pass

    def cleanup_worktree(self, repo_path: str) -> None:
        """Remove the last worktree after changes have been collected."""
        wt = getattr(self, 'last_worktree_path', None)
        if wt:
            self._remove_worktree(repo_path, wt)
            self.last_worktree_path = None

    async def _run_local(self, claude_bin: str, repo_path: str, prompt: str, model: str | None, log_callback: LogCallback | None = None) -> str:
        cmd = [claude_bin, '--print', '--dangerously-skip-permissions']
        if model:
            cmd.extend(['--model', model])
        cmd.extend(['--prompt', prompt])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        collected: list[str] = []
        line_buffer = ''
        log_line_count = 0

        async def _stream_stdout() -> None:
            nonlocal line_buffer, log_line_count
            assert proc.stdout
            while True:
                chunk = await proc.stdout.read(4096)
                if not chunk:
                    break
                text = chunk.decode('utf-8', errors='ignore')
                collected.append(text)
                if log_callback:
                    line_buffer += text
                    while '\n' in line_buffer:
                        line, line_buffer = line_buffer.split('\n', 1)
                        line = line.strip()
                        if not line:
                            continue
                        if log_line_count >= 200:
                            continue
                        preview = line[:300] + ('...' if len(line) > 300 else '')
                        await log_callback(f'CLI: {preview}')
                        log_line_count += 1

        try:
            await asyncio.wait_for(_stream_stdout(), timeout=self.EXEC_TIMEOUT_SEC)
            await proc.wait()
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f'claude timed out after {self.EXEC_TIMEOUT_SEC}s')

        if proc.returncode != 0:
            err = await proc.stderr.read() if proc.stderr else b''
            msg = err.decode('utf-8', errors='ignore').strip() or ''.join(collected).strip()
            raise RuntimeError(f'claude failed: {msg[:300]}')

        content = ''.join(collected).strip()
        if not content:
            raise RuntimeError('claude returned empty output')
        return content

    async def _run_bridge(self, repo_path: str, prompt: str, model: str | None, log_callback: LogCallback | None = None, task_id: str = '') -> str:
        import json as _json
        import httpx

        bridge_url = os.getenv('CLI_BRIDGE_URL', 'http://cli-bridge:9876')
        # Fail fast with a clear message when Claude CLI session is not authenticated.
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
                health = await client.get(f'{bridge_url}/health')
                if health.status_code >= 400:
                    raise RuntimeError(f'CLI bridge health check failed ({health.status_code})')
                h = health.json() if health.content else {}
                if not bool((h or {}).get('claude', False)):
                    raise RuntimeError('Claude CLI not installed on host bridge')
                if not bool((h or {}).get('claude_auth', False)):
                    raise RuntimeError('Claude CLI not authenticated (claude_auth=false)')
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f'CLI bridge health check failed: {exc}')

        # Stream endpoint — real-time logs via SSE (bridge uses --output-format stream-json)
        try:
            collected_text: list[str] = []
            log_line_count = 0
            # Bridge forwards Claude's final result event with real
            # input/output/cache token counts. We stash it on `self`
            # so the orchestration layer can pull it off after
            # generate_file_markdown returns and store accurate
            # usage on the run record (instead of the old len/4 estimate).
            self.last_usage: dict | None = None
            self.last_cost_usd: float | None = None
            self.last_num_turns: int | None = None
            self.last_duration_ms: int | None = None
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.EXEC_TIMEOUT_SEC + 10, connect=10)) as client:
                async with client.stream(
                    'POST',
                    f'{bridge_url}/claude/stream',
                    json={
                        'repo_path': repo_path,
                        'prompt': prompt,
                        'model': model or '',
                        'timeout': self.EXEC_TIMEOUT_SEC,
                        'task_id': task_id,
                    },
                ) as resp:
                    error_msg = None
                    async for raw_line in resp.aiter_lines():
                        if not raw_line.startswith('data: '):
                            continue
                        try:
                            event = _json.loads(raw_line[6:])
                        except (ValueError, TypeError):
                            continue

                        etype = event.get('type', '')
                        if etype == 'text':
                            # Partial text delta from Claude
                            text = event.get('text', '')
                            if text:
                                collected_text.append(text)
                        elif etype == 'tool':
                            # Tool usage event — log it for live display
                            summary = event.get('summary', '')
                            if log_callback and summary and log_line_count < 200:
                                await log_callback(summary)
                                log_line_count += 1
                        elif etype == 'line':
                            # Fallback raw line
                            text = event.get('text', '')
                            if text:
                                collected_text.append(text + '\n')
                            if log_callback and log_line_count < 200 and text.strip():
                                preview = text[:300] + ('...' if len(text) > 300 else '')
                                await log_callback(f'CLI: {preview}')
                                log_line_count += 1
                        elif etype == 'result':
                            text = event.get('text', '')
                            if text:
                                # result contains the final assembled output — use it
                                # as primary content if we haven't collected much text
                                if not collected_text or sum(len(t) for t in collected_text) < len(text):
                                    collected_text.clear()
                                    collected_text.append(text)
                            # Capture real usage / cost / turns from
                            # Claude's final result event (bridge
                            # forwards them since the JSON-output patch).
                            usage_blob = event.get('usage')
                            if isinstance(usage_blob, dict):
                                self.last_usage = usage_blob
                            cost = event.get('cost_usd')
                            if isinstance(cost, (int, float)):
                                self.last_cost_usd = float(cost)
                            num_turns = event.get('num_turns')
                            if isinstance(num_turns, int):
                                self.last_num_turns = num_turns
                            dur_ms = event.get('duration_ms')
                            if isinstance(dur_ms, int):
                                self.last_duration_ms = dur_ms
                            if log_callback and text:
                                await log_callback(f'CLI result: {text[:200]}')
                        elif etype == 'event':
                            pass  # other lifecycle events
                        elif etype == 'stderr':
                            pass
                        elif etype == 'error':
                            error_msg = event.get('message', 'unknown error')
                        elif etype == 'done':
                            if event.get('code', 0) != 0 and not collected_text:
                                error_msg = error_msg or 'claude exited with non-zero code'

                    if error_msg and not collected_text:
                        raise RuntimeError(f'claude bridge error: {error_msg}')

            # Log summary of what was collected
            if log_callback:
                total_chars = sum(len(t) for t in collected_text)
                await log_callback(f'CLI completed: {total_chars} chars output')

            content = ''.join(collected_text).strip()
            if 'Failed to authenticate' in content and 'API Error: 401' in content:
                raise RuntimeError('Claude CLI authentication failed (401). Reconnect Claude from Integrations and try again.')
            if not content:
                raise RuntimeError('claude bridge returned empty output')
            return content

        except httpx.ConnectError:
            raise RuntimeError(f'CLI bridge unreachable at {bridge_url} — is the cli-bridge service running?')
        except httpx.TimeoutException:
            raise RuntimeError(f'CLI bridge request timed out after {self.EXEC_TIMEOUT_SEC}s')
        except RuntimeError:
            raise
        except (httpx.RequestError, Exception) as exc:
            raise RuntimeError(f'CLI bridge request failed: {exc}')

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        timeout_sec: int = 120,
    ) -> str:
        """Lightweight text-in / text-out wrapper around the CLI bridge.

        Intended for short one-shot generations (e.g. a nudge comment) —
        does NOT touch a repo, does NOT create a worktree, and short-circuits
        on the first `result` frame the bridge emits. Uses the bridge's
        /claude/stream endpoint with /tmp as a harmless working dir.
        """
        import json as _json
        import httpx

        bridge_url = os.getenv('CLI_BRIDGE_URL', 'http://cli-bridge:9876')
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
            try:
                health = await client.get(f'{bridge_url}/health')
            except Exception as exc:
                raise RuntimeError(f'CLI bridge unreachable: {exc}')
            if health.status_code >= 400:
                raise RuntimeError(f'CLI bridge health check failed ({health.status_code})')
            h = health.json() if health.content else {}
            if not bool((h or {}).get('claude', False)):
                raise RuntimeError('Claude CLI is not installed on the host bridge')
            if not bool((h or {}).get('claude_auth', False)):
                raise RuntimeError('Claude CLI is not authenticated on the host bridge')

        # Compose prompt (Claude CLI has no system/user split on the CLI
        # side — merge into one instruction block).
        full_prompt = (
            f'{system_prompt.strip()}\n\n---\n{user_prompt.strip()}\n\n'
            'Respond with ONLY the comment text. No preamble, no code blocks, no markdown — plain text only.'
        )

        collected: list[str] = []
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_sec + 10, connect=10)) as client:
            async with client.stream(
                'POST',
                f'{bridge_url}/claude/stream',
                json={
                    'repo_path': '/tmp',
                    'prompt': full_prompt,
                    'model': model or 'sonnet',
                    'timeout': timeout_sec,
                    'task_id': '',
                },
            ) as resp:
                async for raw in resp.aiter_lines():
                    if not raw.startswith('data: '):
                        continue
                    try:
                        event = _json.loads(raw[6:])
                    except (ValueError, TypeError):
                        continue
                    etype = event.get('type', '')
                    if etype == 'text':
                        txt = event.get('text', '')
                        if txt:
                            collected.append(txt)
                    elif etype == 'result':
                        txt = event.get('text', '')
                        if txt:
                            collected.clear()
                            collected.append(txt)
        out = ''.join(collected).strip()
        if not out:
            raise RuntimeError('Claude CLI returned empty output')
        return out
