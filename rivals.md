# Agena — Rakip & Benzer Ürünler (Pazar Analizi)

> Amaç: "Şu ürün bize benziyor mu / hangi açıdan?" sorularını tutarlı bir zeminde
> yanıtlamak için canlı referans. Pazar hızlı değişiyor — her güncellemede tarih düş.
>
> Son güncelleme: 2026-06-16 · Kapsam: web araştırması (2026 Q2) + kod tabanı bilgisi.

---

## 0. Agena'nın konumu (karşılaştırma temeli)

Agena = **kendi-barındırılabilir, çok-kiracılı, iş-öğesi → tam-teslimat platformu.**

Çekirdek döngü:
**iş öğesi (Azure DevOps / Jira / Sentry / New Relic) → AI analiz → kod üret → PR aç →
AI code review → merge → CI/CD pipeline → deploy/pipeline ONAY KAPISI → deploy.**

Ayırt edici özellik kombinasyonu (hepsi bir arada, tek pakette):
- Self-host **+** çok-kiracılı (multi-tenant)
- **Azure DevOps** birinci sınıf intake/akış (çoğu rakip GitHub-merkezli) + Sentry/New Relic
- Görsel **flow builder** (n8n-tarzı node'lar: agent/condition/http/azure/github/notify)
- **Multi-repo orkestrasyon** (tek iş → N repo → N PR)
- **İnsan onay kapıları** — özellikle **harici** bir orkestratör olarak **Azure Pipelines deploy onaylarını** sürme + **KNOWN/FOREIGN sınıflandırması** (yanlış pipeline onaylama koruması)
- Entegre **DevOps Board** (WI→PR→review→merge→build→approval) + Prompt Studio
- Altında **Claude/Codex CLI** sürer (kendi modelini koşmaz)

### ⚠️ Neden çoğu ürün BİREBİR benzemiyor (önemli)
Aşağıdaki ürünlerin hiçbiri şu kombinasyonu tam tutmuyor. Tipik ayrışmalar:
- **IDE-merkezli** (Cursor, Windsurf, Cline, Continue, Copilot autocomplete) → orkestratör değil.
- **PR'da durur** (Devin, Jules, Sweep, OpenHands) → kendi **deploy/pipeline onay kapısı yok**.
- **Ekosisteme kilitli** (GitLab Duo, GitHub Copilot agent) → gate'i "bedava" gelir çünkü CI/CD'nin sahibi onlar; **harici/Azure** senaryosu yok.
- **Genel orkestrasyon** (Dify, n8n, LangGraph) → SWE-domain'e özel değil (PR/review/CI bağlamı gömülü değil).
- **Sadece review** (CodeRabbit, Greptile) ya da **sadece board** (Backstage, Port) → tek katman.

**En yakın kavramsal ikizler:** **Factory.ai** (ticari) ve **OpenHands** (açık kaynak) — ama
ikisi de self-host+çok-kiracılı+Azure+flow+sınıflandırılmış-pipeline-onay setini tam vermiyor.

---

## A. Otonom AI yazılım platformları (en yakın kategori: iş → kod → PR)

### A.1 Açık kaynak (self-host) — en alakalı alternatifler
| Ürün | Lisans | Tek satır | Agena'ya yakınlık |
|---|---|---|---|
| **OpenHands** (eski OpenDevin, All-Hands AI) | MIT | Issue→PR ajan platformu; GitHub/GitLab/**Jira/Linear**/Slack, K8s self-host, multi-repo | **OSS'de EN YAKIN.** Eksik: gerçek çok-kiracılı değil, görsel flow yok, **deploy onay kapısı yok** (PR'da durur) |
| **SWE-agent** (Princeton) | MIT | GitHub issue→PR; SWE-bench | Tek-repo, kurumsal/gate yok |
| **Aider** | Apache-2.0 | CLI pair-programmer, git-native | Etkileşimli, issue-tracker yok |
| **Cline / Roo Code** | MIT | VS Code otonom ajanı | IDE-merkezli, platform değil |
| **Continue.dev** | Apache-2.0 | Çok-IDE Copilot alternatifi | Tamamlama/düzenleme ağırlıklı |
| **Goose** (Block) | Apache-2.0 | Açık ajan framework'ü (CLI) | Issue-tracker entegre değil |
| **Plandex** | MIT | Terminal ajanı, büyük kod tabanı | Tek-repo |
| **Devika / gptme / Tabby** | MIT | Devika: Devin-klonu (erken); Tabby: self-host tamamlama (ajan değil) | Düşük olgunluk / kapsam dışı |

### A.2 Ticari / popüler
| Ürün | Tek satır | Agena'ya yakınlık |
|---|---|---|
| **Factory.ai (Droids)** | Rol-bazlı droid orkestrasyonu; Jira/Linear/GitHub/Sentry; multi-repo, kurumsal SDLC | **Ticaride EN YAKIN.** Eksik: proprietary, **self-host yok**, deploy gate'i SCM'den miras |
| **Devin** (Cognition) | Otonom issue→kod→test→PR; cloud-only | Kara kutu, gate PR'da durur |
| **GitHub Copilot coding agent** | issue ata→async VM→draft PR (Actions) | GitHub-native; deploy gate = **GitHub Environments** (native ama ekosisteme kilitli) |
| **OpenAI Codex** (cloud agent) | repo→çok-dosya→PR; ChatGPT/CLI | GitHub-native, gate yok |
| **Google Jules** | Gemini async ajan, CI-fixer | Test-dayanıklı; deploy gate yok |
| **Cursor / Windsurf** | IDE + background/cloud agent | Tek-task, orkestrasyon/gate yok |
| **Amazon Q Developer** | AWS-native ajan + kod dönüşümü | AWS-kilitli |
| **Augment, Qodo Gen, Zencoder, Sweep, Solver** | Augment: kurumsal context-engine (hybrid on-prem); Sweep: GitHub issue→PR (cloud) | Parça çözümler |

---

## B. AI Code Review (Agena'nın review + merge-gate katmanı)
| Ürün | Lisans | Not |
|---|---|---|
| **PR-Agent / Qodo Merge** | **Apache-2.0 (OSS)** | Tek büyük açık kaynak review; repo-config, GH/GL/BB/**Azure DevOps**, multi-agent |
| **CodeRabbit** | Ticari | Pazar lideri, `.coderabbit.yaml` |
| **Greptile** | Ticari | Tam kod-tabanı indeksleme, yüksek bug-yakalama |
| **Graphite (Diamond), Sourcery, Codacy, CodeAnt, Bito, Ellipsis** | Ticari | Codacy: review+güvenlik; Ellipsis: otomatik fix |

OSS'de pratikte tek ciddi alternatif: **PR-Agent**.

---

## C. Orkestrasyon / Flow motoru (Agena'nın görsel flow katmanı)
| Ürün | Lisans | Görsel? | Not |
|---|---|---|---|
| **Dify** | Apache-2.0 (SaaS kısıtlı) | ✅ | En hızlı büyüyen LLMOps platformu, çok-kiracılı |
| **n8n** | Fair-Code | ✅ | Kurumsal otomasyon + AI node |
| **Flowise / Langflow / Activepieces** | Apache-2.0 / OSS | ✅ | LangChain görsel builder'ları |
| **LangGraph** *(Agena kullanıyor)* | MIT | ❌ | Stateful ajan standardı (kod-bazlı) |
| **CrewAI** *(Agena kullanıyor)* | MIT | ❌ | Rol-bazlı çoklu-ajan |
| **AutoGen / AG2, OpenAI Agents SDK** | MIT / ticari | ❌ | Genel çoklu-ajan |
| **Temporal** | OSS | ❌ | Durable execution (bizim "durable async" işimizin endüstri karşılığı) |

Hepsi **genel** orkestrasyon; Agena'nın farkı **SWE-domain'e özel**.

---

## D. Internal Developer Portal / Delivery board (Agena'nın DevOps Board'u)
| Ürün | Lisans | Not |
|---|---|---|
| **Backstage** (Spotify) | **Apache-2.0 (tek OSS)** | Servis kataloğu; CI/CD/PR/deploy plugin'le |
| **Port, Cortex, OpsLevel, Sleuth, Cycloid** | Ticari SaaS | Port/Cortex: PR+deploy tek pane; Sleuth: DORA+deploy; OpsLevel: AI katalog |
| **Atlassian Compass** | Ticari | Kullanımdan kalkıyor → **DX Fabric** |

---

## E. "Tam döngü + pipeline/deploy ONAY KAPISI" olanlar (en kritik ayraç) — Top 5
> Ayraç: sadece issue→PR değil, **deploy/pipeline onay kapısı** da döngüde olanlar.

| # | Ürün | Tip | Deploy onayı: kendi mi / miras mı? |
|---|---|---|---|
| 1 | **GitLab Duo + GitLab CI/CD** | Ticari (self-managed var) | ✅ Native (protected environments / deployment approvals) — **ama GitLab ekosistemine kilitli** |
| 2 | **GitHub Copilot coding agent + GitHub Actions Environments** | Ticari | ✅ Native (required reviewers) — **GitHub'a kilitli** |
| 3 | **Factory.ai (Droids)** | Ticari | ⚠️ Miras (SCM branch-protection); kendi gate'i değil |
| 4 | **Atlassian Rovo Dev + Bitbucket + JSM** | Ticari | ✅ JSM deployment gating (ayrı ürün) |
| 5 | **OpenHands** | **OSS (MIT)** | ❌ Kendi gate'i yok — PR'da durur |

**Tam kapatmayanlar:** Devin, Google Jules, Cursor, Sweep (PR'da durur).

**Agena'nın özgün yeri:** Döngüyü + deploy onay kapısını **Azure DevOps Pipelines** üzerinde,
**harici bir orkestratör** olarak kurar ve onayları **KNOWN/FOREIGN sınıflandırmasıyla** güvene
alır. Bu (harici + Azure + sınıflandırılmış onay + self-host + çok-kiracılı + flow) kombinasyonu
yukarıdaki hiçbirinde yok.

---

## F. Karşılaştırma rubriği (yeni bir ürün "benziyor mu?" diye sorulduğunda kullan)

Bir rakibi Agena'ya kıyaslarken şu 12 maddeyi işaretle — kaç maddeyi tutuyor?

1. **Self-host** edilebilir mi? (cloud-only mu?)
2. **Çok-kiracılı (multi-tenant)** platform mu?
3. **İş-öğesi intake** — hangi tracker'lar? (Azure Boards / Jira / GitHub / Linear / Sentry / NewRelic)
4. **Otonom geliştirme → PR** açıyor mu? (yoksa IDE asistanı mı?)
5. **AI code review** var mı?
6. **Merge** ediyor mu / **CI tetikliyor** mu?
7. **Deploy/pipeline ONAY KAPISI** var mı — **kendi mi**, yoksa SCM/CI'dan **miras** mı?
8. **Multi-repo orkestrasyon** (tek iş → N repo, sıralı/paralel) var mı?
9. **Görsel flow/orkestrasyon builder** var mı?
10. **Azure DevOps** birinci sınıf destek mi? (yoksa GitHub-merkezli mi?)
11. **Kendi modelini mi** koşar yoksa **CLI/harici LLM** mi sürer?
12. **OSS mi** (lisans?) yoksa ticari mi?

> Pratik kural: 8+ madde tutuyorsa "gerçek rakip"; 4–7 ise "bitişik/örtüşen"; <4 ise "farklı kategori".
> Bugüne kadar **12/12 tutan tek ürün yok**; en yüksek skor Factory.ai (ticari) ve OpenHands (OSS).

---

## Kaynak notu
Bu dosya 2026-Q2 web araştırması + Agena kod tabanına dayanır. Ürün özellikleri/lisansları/
fiyatları hızla değişir — bir rakip hakkında karar vermeden önce o ürünün güncel dokümanını teyit et.
