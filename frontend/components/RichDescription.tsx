'use client';

import { useEffect, useMemo, useRef } from 'react';
import { apiDownloadBlob } from '@/lib/api';
import { renderMarkdown } from '@/lib/markdown';

const AUTH_IMAGE_HOST_RE = /(dev\.azure\.com\/|\/_apis\/wit\/attachments\/|atlassian\.net\/)/i;

/**
 * Renders a task description (Markdown / sanitized HTML from
 * `lib/markdown.renderMarkdown`) and, after mount, re-routes any
 * authenticated remote images (Azure DevOps / Jira attachments) through
 * the backend's image proxy so they actually load — `<img src>` can't
 * carry Authorization headers, but our `apiDownloadBlob` can.
 *
 * Blob URLs are cached by source across re-renders and revoked only on
 * unmount. The task detail page polls live-activity frequently; the old
 * code revoked every blob on each re-render, so the image kept flickering
 * back to a broken state mid-run ("image sometimes doesn't load"). Caching
 * by src means a re-render instantly re-applies the already-fetched blob
 * instead of revoking + re-fetching (and briefly showing the broken raw
 * Azure URL in the gap).
 */
export default function RichDescription({
  html,
  className,
  style,
}: {
  html: string;
  className?: string;
  style?: React.CSSProperties;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  // src → object URL. Persists across renders so re-renders reuse the
  // already-proxied blob instead of revoking and re-fetching it.
  const blobCache = useRef<Map<string, string>>(new Map());
  const renderedHtml = useMemo(() => renderMarkdown(html || ''), [html]);

  useEffect(() => {
    if (!ref.current) return;
    let cancelled = false;
    const imgs = Array.from(ref.current.querySelectorAll('img'));
    imgs.forEach((img) => {
      const src = img.getAttribute('src') || '';
      if (!AUTH_IMAGE_HOST_RE.test(src)) return;
      // Already proxied this source — re-apply instantly (no flicker, no refetch).
      const cached = blobCache.current.get(src);
      if (cached) {
        img.setAttribute('src', cached);
        return;
      }
      apiDownloadBlob(`/tasks/proxy-image?url=${encodeURIComponent(src)}`)
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          blobCache.current.set(src, url);
          if (cancelled) return;
          // The element may have been replaced by a re-render; re-query by
          // the original src so we update whatever is currently mounted.
          const node = ref.current;
          if (!node) return;
          node.querySelectorAll('img').forEach((el) => {
            if ((el.getAttribute('src') || '') === src) el.setAttribute('src', url);
          });
        })
        .catch(() => {
          // Leave the original src — browser shows a broken-image icon
          // for unauthenticated viewers, which is preferable to silently
          // hiding context the user uploaded.
        });
    });
    return () => {
      cancelled = true;
    };
  }, [renderedHtml]);

  // Revoke cached object URLs only when the component truly unmounts.
  useEffect(() => {
    const cache = blobCache.current;
    return () => {
      cache.forEach((u) => URL.revokeObjectURL(u));
      cache.clear();
    };
  }, []);

  return (
    <div
      ref={ref}
      className={className}
      style={style}
      dangerouslySetInnerHTML={{ __html: renderedHtml }}
    />
  );
}
