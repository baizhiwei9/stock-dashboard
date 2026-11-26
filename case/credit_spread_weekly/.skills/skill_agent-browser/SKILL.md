---
name: agent-browser
description: 真实浏览器自动化工具，操控真实浏览器实例（非模拟器）完成：网页导航与访问、表单填写与提交、按钮点击与元素交互、页面/全页面截图、网页数据抓取与内容提取、Web 应用自动化测试、登录认证（支持用户接管模式，适用于扫码登录、短信验证等需要人工介入的场景）、支付等敏感操作的安全处理。当用户提出「打开某个网站」「填写表单」「点击按钮」「截图」「抓取页面数据」「测试这个网页」「登录某个网站」「自动化浏览器操作」，或任何需要在真实浏览器中执行的交互任务时使用。支持多会话并行、状态持久化、设备模拟、视觉对比等高级功能。注意：本 skill 仅在云主机环境（device_type=cloud）下可用；当前会话为 local/local_computer 或 vefaas 等非云主机设备时不要使用，并按 SKILL 正文的 Device Type Gate 门控给出对应提示。
allowed-tools: Bash(agent-browser:*), agent_browser_status, browser_wait_user_action, task, file_to_url
---

# Browser Automation with agent-browser

## Precondition: Device Type Gate (MANDATORY — CHECK FIRST)

This skill drives a **real cloud browser** and only works when the current session runs on a cloud host. **Before doing anything else** (before `agent_browser_status`, before any `agent-browser` command, before opening a sub-session), read `<device><device_type>` from the current session-context environment snapshot and branch:

- **`device_type == cloud`** → 允许，正常按下面的流程执行。
- **`device_type == local` 或 `local_computer`** → **不要**调用本 skill，直接回复用户：
  > 当前使用本地电脑，无法发起云主机调用。可切换到本地工具 browser_use 执行。
- **`device_type == vefaas`** → **不要**调用本 skill，直接回复用户：
  > 建议添加云主机设备（需要 个人高阶版、个人旗舰版或企业版才有云电脑权益）
- **其它/未知取值** → 保守处理，不调用本 skill，并告知用户当前设备环境不支持云主机浏览器操作。

只有确认 `device_type == cloud` 后，才继续下面的 Agent Guidelines 与工作流。命中非 cloud 分支时，输出上述对应话术后**结束回合**，不要再执行任何 `agent-browser` 命令。

## Agent Guidelines & Restrictions

1. **NO Command Line Output to User**: Do NOT output any terminal commands (like `agent-browser ...`) to the user. The user cannot execute these commands. If you need the user to make a choice or provide input, use clear, natural language to ask them.
2. **Lifecycle Reporting & User Takeover (IMPORTANT)**: Report the browser task lifecycle to the frontend:
   - **Start**: When the browser task begins, call `agent_browser_status(status="start", title=...)` once so the frontend shows the browser card. `title` is optional (defaults to「浏览器任务」).
   - **Waiting (takeover) — MANDATORY, NON-NEGOTIABLE**: The **only** correct way to hand a sensitive step to the user is to call `browser_wait_user_action(title=..., description=...)`. You **MUST** call it — this is not optional and there is no alternative tool for takeover. `title` is required; `description` should tell the user exactly what to do (e.g. "请在浏览器中完成扫码登录").
     - **NEVER** attempt the sensitive step yourself: do NOT type passwords / verification codes / payment details, do NOT guess or fabricate credentials, do NOT click through a payment or "confirm" button. Stop and call `browser_wait_user_action` **before** the sensitive action, not after.
     - After the call succeeds, tell the user in natural language that their manual action is required, then **end your turn and wait** — do NOT keep issuing `agent-browser` commands. The user completes the step in the live browser and sends a new message to resume; there is no other resume path.
     - Call it **once per takeover**. If it has already been called for the current pending step, do NOT call it again — just wait.
     - You are **not** limited to QR-code login; use it for any human-only step below.
   - **Finish**: When the browser task is fully complete, call `agent_browser_status(status="finish")` exactly once so the frontend can close the browser card. `title` is optional.

   Call `browser_wait_user_action` and hand control to the user whenever any of the following is required (this list is not exhaustive — when in doubt, hand over):
   - Entering passwords, verification codes, or fingerprint authentication
   - Confirming payments, transfers, or subscription charges
   - Permanently deleting data or deactivating/closing accounts
   - Changing passwords or security settings
   - Registering a new account
   - Granting sensitive permissions (contacts, location, microphone, etc.)
3. **Screenshots**: When capturing screenshots, **ALL** screenshot files MUST be saved under `/Coze/Drive/<agent_name>/browser/screenshots/`, where `<agent_name>` is the name of the current conversation's agent (usually recorded as the `名字` / name field in `SOUL.md` — you already know it during the chat). For different tasks, create a separate subdirectory named after the task so screenshots stay independent and the directory structure is clear (e.g., `agent-browser screenshot /Coze/Drive/Luca/browser/screenshots/task_name/page.png`). If you genuinely cannot determine the agent name, fall back to `default` (i.e. `/Coze/Drive/default/browser/screenshots/task_name/page.png`).
   - **Showing screenshots to the user (IMPORTANT)**: The screenshot path returned by agent-browser (e.g. `computer:///Coze/Drive/...` or any host-local path) is a path *inside the sandbox host* — the user CANNOT open it. When you need to present a screenshot to the user (render it, or give them a link), you MUST first pass the saved file to the `file_to_url` tool to obtain an accessible URL, then show that URL. Never send the raw `computer://` / host path to the user. (For your own internal analysis — e.g. reading the screenshot yourself — the local path is fine; no conversion needed.)
4. **Background Execution (Sub-Session) — MANDATORY**: You MUST NOT run any `agent-browser` command in the main session. ALWAYS run browser automation inside a background sub-session created via the `task` tool with `run_in_background: true`. Running `agent-browser` directly in the main session will be **rejected by the runtime** (the command returns an error telling you to use `task`). **CRITICAL**: The sub-session MUST report the execution status of the current task back to the main session, and explicitly state whether `browser_wait_user_action` has been called. If it has already been called for the current takeover, do NOT call it repeatedly.
5. **Profile Lock Resolution**: If you encounter an error indicating that the profile file is locked, you can try deleting the `$AGENT_BROWSER_PROFILE/Singleton*` lock files.
6. **Forced Headed Mode (MANDATORY)**: This skill runs the browser in **headed** (visible-window) mode, never headless. In the browser sub-session, **before** running any `agent-browser` command, export the environment variable once:

   ```bash
   export AGENT_BROWSER_HEADED=1
   ```

   All subsequent `agent-browser` commands in that sub-session then run headed automatically — do NOT add `--headed` to individual commands. Never disable this (do not pass `--headed false` or set `AGENT_BROWSER_HEADED=0`). Headed mode requires a graphical desktop in the execution environment; if the browser fails to launch because no display is available, report this to the user rather than silently falling back to headless.
7. ***Persistent Browser*: The lifecycle of a background daemon in a background sub-session is the same as that of the Bash command. Therefore, if browser operations need to persist across commands (for example, opening the interface and waiting for the user to log in), first start a keepalive container and then dispatch the task to a sub-session as follows:
  - Start the keepalive container: Use background Bash in the main session to run the following command. If the user does not specify the keepalive duration, default to 1200 seconds:
    ```bash
    export DISPLAY=:99
    setsid /usr/bin/google-chrome --no-sandbox --remote-debugging-port=<PORT> \
      --no-first-run --no-default-browser-check --user-data-dir=<profile-directory> \
      "<target-URL>" >/tmp/chrome.log 2>&1 </dev/null & sleep <keepalive-seconds>
    ```
    - First, the keepalive container must be run by starting background Bash in the main session. Second, no directory or command may contain the string `agent-browser`, otherwise an error will occur.
    - The sleep keepalive duration for background Bash must be specified through sleep <keepalive-seconds>, using 1200 seconds by default; do not specify it through the Bash tool
  - Dispatch a task sub-session to execute the task, and include the following in the prompt:
    - Before each Bash command, first export AGENT_BROWSER_HEADED=1; execute synchronously only, and do not keep alive, wait, launch a new Chrome instance, or close
    - Include --cdp http://127.0.0.1:<PORT> --session <fixed-name> in every command, and operate only on the tab for this task
    - First verify the connection independently. If it fails, do not retry repeatedly or remove --cdp; report the failure accurately
  - Renew:
    - Repeat step 1 with the same PORT and the same user-data-dir; the login state will be preserved.
    - Notify the user after expiration. Do not renew automatically without user instructions.
  - Cancel or close (in order):
    - Close the browser:
      ```bash
      NODE_PATH=/usr/lib/node_modules/openclaw/node_modules node -e '
      const http=require("http"),WS=require("ws");
      http.get("http://127.0.0.1:<PORT>/json/version",r=>{let d="";r.on("data",c=>d+=c);
      r.on("end",()=>{const w=new WS(JSON.parse(d).webSocketDebuggerUrl);
      w.on("open",()=>w.send(JSON.stringify({id:1,method:"Browser.close"})));
      w.on("message",()=>setTimeout(()=>process.exit(0),1500));});});'
      ```
    - Verify that the keepalive container has closed:
      - curl /json/version fails
    - Use stop_task to stop the task_id of the keepalive container started in step 1
    - agent_browser_status(finish) 
    - Implement other agent-browser-related lifecycle or session management in the task according to the requirements below. The steps above primarily handle closing the browser and the keepalive container.

## Core Workflow

Every browser automation follows this pattern:

1. **Navigate**: `agent-browser open <url>` && agent-browser tab 0
2. **Snapshot**: `agent-browser snapshot -i` (get element refs like `@e1`, `@e2`)
3. **Interact**: Use refs to click, fill, select
4. **Re-snapshot**: After navigation or DOM changes, get fresh refs

```bash
agent-browser open https://example.com/form && agent-browser tab 0
agent-browser snapshot -i
# Output: @e1 [input type="email"], @e2 [input type="password"], @e3 [button] "Submit"

agent-browser fill @e1 "user@example.com"
agent-browser fill @e2 "password123"
agent-browser click @e3
agent-browser wait --load networkidle
agent-browser snapshot -i  # Check result
```

## Command Chaining

Commands can be chained with `&&` in a single shell invocation. The browser persists between commands via a background daemon, so chaining is safe and more efficient than separate calls. Chaining is especially recommended when the browser behaves unexpectedly with commands run individually or when `about:blank` pages appear frequently.

```bash
# Chain open + wait + snapshot in one call
agent-browser open https://example.com && agent-browser tab 0 && agent-browser wait --load networkidle && agent-browser snapshot -i

# Chain multiple interactions
agent-browser fill @e1 "user@example.com" && agent-browser fill @e2 "password123" && agent-browser click @e3

# Navigate and capture
agent-browser open https://example.com && agent-browser tab 0 && agent-browser wait --load networkidle && agent-browser screenshot page.png
```

**When to chain:** Use `&&` when you don't need to read the output of an intermediate command before proceeding (e.g., open + wait + screenshot). Run commands separately when you need to parse the output first (e.g., snapshot to discover refs, then interact using those refs).

## Essential Commands

```bash
# Navigation
agent-browser open <url> && agent-browser tab 0  # Navigate (aliases: goto, navigate)
agent-browser close                   # Close browser

# Snapshot
agent-browser snapshot -i             # Interactive elements with refs (recommended)
agent-browser snapshot -i -C          # Include cursor-interactive elements (divs with onclick, cursor:pointer)
agent-browser snapshot -s "#selector" # Scope to CSS selector

# Interaction (use @refs from snapshot)
agent-browser click @e1               # Click element
agent-browser click @e1 --new-tab     # Click and open in && agent-browser click @e1 --new-tab && agent-browser tab 1  # Click and tab 0 new tab
agent-browser fill @e2 "text"         # Clear and type text
agent-browser type @e2 "text"         # Type without clearing
agent-browser select @e1 "option"     # Select dropdown option
agent-browser check @e1               # Check checkbox
agent-browser press Enter             # Press key
agent-browser keyboard type "text"    # Type at current focus (no selector)
agent-browser keyboard inserttext "text"  # Insert without key events
agent-browser scroll down 500         # Scroll page
agent-browser scroll down 500 --selector "div.content"  # Scroll within a specific container

# Get information
agent-browser get text @e1            # Get element text
agent-browser get url                 # Get current URL
agent-browser get title               # Get page title

# Wait
agent-browser wait @e1                # Wait for element
agent-browser wait --load networkidle # Wait for network idle
agent-browser wait --url "**/page"    # Wait for URL pattern
agent-browser wait 2000               # Wait milliseconds

# Downloads
agent-browser download @e1 ./file.pdf          # Click element to trigger download
agent-browser wait --download ./output.zip     # Wait for any download to complete
agent-browser --download-path ./downloads open <url> && agent-browser --download-path ./downloads tab 0  # Set default download directory

# Viewport & Device Emulation
agent-browser set viewport 1920 1080          # Set viewport size (default: 1280x720)
agent-browser set viewport 1920 1080 2        # 2x retina (same CSS size, higher res screenshots)
agent-browser set device "iPhone 14"          # Emulate device (viewport + user agent)

# Capture
agent-browser screenshot              # Screenshot to temp dir
agent-browser screenshot --full       # Full page screenshot
agent-browser screenshot --annotate   # Annotated screenshot with numbered element labels
agent-browser pdf output.pdf          # Save as PDF

# Diff (compare page states)
agent-browser diff snapshot                          # Compare current vs last snapshot
agent-browser diff snapshot --baseline before.txt    # Compare current vs saved file
agent-browser diff screenshot --baseline before.png  # Visual pixel diff
agent-browser diff url <url1> <url2>                 # Compare two pages
agent-browser diff url <url1> <url2> --wait-until networkidle  # Custom wait strategy
agent-browser diff url <url1> <url2> --selector "#main"  # Scope to element
```

## Common Patterns

### Form Submission

```bash
agent-browser open https://example.com/signup && agent-browser tab 0
agent-browser snapshot -i
agent-browser fill @e1 "Jane Doe"
agent-browser fill @e2 "jane@example.com"
agent-browser select @e3 "California"
agent-browser check @e4
agent-browser click @e5
agent-browser wait --load networkidle
```

### Authentication with Auth Vault (Recommended)

```bash
# Save credentials once (encrypted with AGENT_BROWSER_ENCRYPTION_KEY)
# Recommended: pipe password via stdin to avoid shell history exposure
echo "pass" | agent-browser auth save github --url https://github.com/login --username user --password-stdin

# Login using saved profile (LLM never sees password)
agent-browser auth login github

# List/show/delete profiles
agent-browser auth list
agent-browser auth show github
agent-browser auth delete github
```

### Authentication with State Persistence

```bash
# Login once and save state
agent-browser open https://app.example.com/login && agent-browser tab 0
agent-browser snapshot -i
agent-browser fill @e1 "$USERNAME"
agent-browser fill @e2 "$PASSWORD"
agent-browser click @e3
agent-browser wait --url "**/dashboard"
agent-browser state save auth.json

# Reuse in future sessions
agent-browser state load auth.json
agent-browser open https://app.example.com/dashboard && agent-browser tab 0
```

### Session Persistence

```bash
# Auto-save/restore cookies and localStorage across browser restarts
agent-browser --session-name myapp open https://app.example.com/login && agent-browser --session-name myapp tab 0
# ... login flow ...
agent-browser close  # State auto-saved to ~/.agent-browser/sessions/

# Next time, state is auto-loaded
agent-browser --session-name myapp open https://app.example.com/dashboard && agent-browser --session-name myapp tab 0

# Encrypt state at rest
export AGENT_BROWSER_ENCRYPTION_KEY=$(openssl rand -hex 32)
agent-browser --session-name secure open https://app.example.com && agent-browser --session-name secure tab 0

# Manage saved states
agent-browser state list
agent-browser state show myapp-default.json
agent-browser state clear myapp
agent-browser state clean --older-than 7
```

### Data Extraction

```bash
agent-browser open https://example.com/products && agent-browser tab 0
agent-browser snapshot -i
agent-browser get text @e5           # Get specific element text
agent-browser get text body > page.txt  # Get all page text

# JSON output for parsing
agent-browser snapshot -i --json
agent-browser get text @e1 --json
```

### Parallel Sessions

```bash
agent-browser --session site1 open https://site-a.com && agent-browser --session site1 tab 0
agent-browser --session site2 open https://site-b.com && agent-browser --session site2 tab 0

agent-browser --session site1 snapshot -i
agent-browser --session site2 snapshot -i

agent-browser session list
```

### Connect to Existing Chrome

```bash
# Auto-discover running Chrome with remote debugging enabled
agent-browser --auto-connect open https://example.com && agent-browser --auto-connect tab 0
agent-browser --auto-connect snapshot

# Or with explicit CDP port
agent-browser --cdp 9222 snapshot
```

### Color Scheme (Dark Mode)

```bash
# Persistent dark mode via flag (applies to all pages and new tabs)
agent-browser --color-scheme dark open https://example.com && agent-browser --color-scheme dark tab 0

# Or via environment variable
AGENT_BROWSER_COLOR_SCHEME=dark agent-browser open https://example.com && agent-browser tab 0

# Or set during session (persists for subsequent commands)
agent-browser set media dark
```

### Viewport & Responsive Testing

```bash
# Set a custom viewport size (default is 1280x720)
agent-browser set viewport 1920 1080
agent-browser screenshot desktop.png

# Test mobile-width layout
agent-browser set viewport 375 812
agent-browser screenshot mobile.png

# Retina/HiDPI: same CSS layout at 2x pixel density
# Screenshots stay at logical viewport size, but content renders at higher DPI
agent-browser set viewport 1920 1080 2
agent-browser screenshot retina.png

# Device emulation (sets viewport + user agent in one step)
agent-browser set device "iPhone 14"
agent-browser screenshot device.png
```

The `scale` parameter (3rd argument) sets `window.devicePixelRatio` without changing CSS layout. Use it when testing retina rendering or capturing higher-resolution screenshots.

### Visual Browser (Debugging)

Headed mode is already forced on for this skill via `AGENT_BROWSER_HEADED=1` (see Agent Guidelines #6), so the browser window is always visible — you do not need `--headed` on individual commands. The tools below help inspect and record what the browser is doing:

```bash
agent-browser highlight @e1          # Highlight element
agent-browser record start demo.webm # Record session
agent-browser profiler start         # Start Chrome DevTools profiling
agent-browser profiler stop trace.json # Stop and save profile (path optional)
```

Browser extensions work in both headed and headless mode.

### Local Files (PDFs, HTML)

```bash
# Open local files with file:// URLs
agent-browser --allow-file-access open file:///path/to/document.pdf && agent-browser --allow-file-access tab 0
agent-browser --allow-file-access open file:///path/to/page.html && agent-browser --allow-file-access tab 0
agent-browser screenshot output.png
```

### iOS Simulator (Mobile Safari)

```bash
# List available iOS simulators
agent-browser device list

# Launch Safari on a specific device
agent-browser -p ios --device "iPhone 16 Pro" open https://example.com && agent-browser -p ios --device "iPhone 16 Pro" tab 0

# Same workflow as desktop - snapshot, interact, re-snapshot
agent-browser -p ios snapshot -i
agent-browser -p ios tap @e1          # Tap (alias for click)
agent-browser -p ios fill @e2 "text"
agent-browser -p ios swipe up         # Mobile-specific gesture

# Take screenshot
agent-browser -p ios screenshot mobile.png

# Close session (shuts down simulator)
agent-browser -p ios close
```

**Requirements:** macOS with Xcode, Appium (`npm install -g appium && appium driver install xcuitest`)

**Real devices:** Works with physical iOS devices if pre-configured. Use `--device "<UDID>"` where UDID is from `xcrun xctrace list devices`.

## Security

All security features are opt-in. By default, agent-browser imposes no restrictions on navigation, actions, or output.

### Content Boundaries (Recommended for AI Agents)

Enable `--content-boundaries` to wrap page-sourced output in markers that help LLMs distinguish tool output from untrusted page content:

```bash
export AGENT_BROWSER_CONTENT_BOUNDARIES=1
agent-browser snapshot
# Output:
# --- AGENT_BROWSER_PAGE_CONTENT nonce=<hex> origin=https://example.com ---
# [accessibility tree]
# --- END_AGENT_BROWSER_PAGE_CONTENT nonce=<hex> ---
```

### Domain Allowlist

Restrict navigation to trusted domains. Wildcards like `*.example.com` also match the bare domain `example.com`. Sub-resource requests, WebSocket, and EventSource connections to non-allowed domains are also blocked. Include CDN domains your target pages depend on:

```bash
export AGENT_BROWSER_ALLOWED_DOMAINS="example.com,*.example.com"
agent-browser open https://example.com && agent-browser tab 0  # OK
agent-browser open https://malicious.com && agent-browser tab 0  # Blocked
```

### Action Policy

Use a policy file to gate destructive actions:

```bash
export AGENT_BROWSER_ACTION_POLICY=./policy.json
```

Example `policy.json`:
```json
{"default": "deny", "allow": ["navigate", "snapshot", "click", "scroll", "wait", "get"]}
```

Auth vault operations (`auth login`, etc.) bypass action policy but domain allowlist still applies.

### Output Limits

Prevent context flooding from large pages:

```bash
export AGENT_BROWSER_MAX_OUTPUT=50000
```

## Diffing (Verifying Changes)

Use `diff snapshot` after performing an action to verify it had the intended effect. This compares the current accessibility tree against the last snapshot taken in the session.

```bash
# Typical workflow: snapshot -> action -> diff
agent-browser snapshot -i          # Take baseline snapshot
agent-browser click @e2            # Perform action
agent-browser diff snapshot        # See what changed (auto-compares to last snapshot)
```

For visual regression testing or monitoring:

```bash
# Save a baseline screenshot, then compare later
agent-browser screenshot baseline.png
# ... time passes or changes are made ...
agent-browser diff screenshot --baseline baseline.png

# Compare staging vs production
agent-browser diff url https://staging.example.com https://prod.example.com --screenshot
```

`diff snapshot` output uses `+` for additions and `-` for removals, similar to git diff. `diff screenshot` produces a diff image with changed pixels highlighted in red, plus a mismatch percentage.

## Timeouts and Slow Pages

The default Playwright timeout is 25 seconds for local browsers. This can be overridden with the `AGENT_BROWSER_DEFAULT_TIMEOUT` environment variable (value in milliseconds). For slow websites or large pages, use explicit waits instead of relying on the default timeout:

```bash
# Wait for network activity to settle (best for slow pages)
agent-browser wait --load networkidle

# Wait for a specific element to appear
agent-browser wait "#content"
agent-browser wait @e1

# Wait for a specific URL pattern (useful after redirects)
agent-browser wait --url "**/dashboard"

# Wait for a JavaScript condition
agent-browser wait --fn "document.readyState === 'complete'"

# Wait a fixed duration (milliseconds) as a last resort
agent-browser wait 5000
```

When dealing with consistently slow websites, use `wait --load networkidle` after `open` to ensure the page is fully loaded before taking a snapshot. If a specific element is slow to render, wait for it directly with `wait <selector>` or `wait @ref`.

## Session Management and Cleanup

When running multiple agents or automations concurrently, always use named sessions to avoid conflicts:

```bash
# Each agent gets its own isolated session
agent-browser --session agent1 open site-a.com && agent-browser --session agent1 tab 0
agent-browser --session agent2 open site-b.com && agent-browser --session agent2 tab 0

# Check active sessions
agent-browser session list
```

Always close your browser session when done to avoid leaked processes:

```bash
agent-browser close                    # Close default session
agent-browser --session agent1 close   # Close specific session
```

If a previous session was not closed properly, the daemon may still be running. Use `agent-browser close` to clean it up before starting new work.

## Ref Lifecycle (Important)

Refs (`@e1`, `@e2`, etc.) are invalidated when the page changes. Always re-snapshot after:

- Clicking links or buttons that navigate
- Form submissions
- Dynamic content loading (dropdowns, modals)

```bash
agent-browser click @e5              # Navigates to new page
agent-browser snapshot -i            # MUST re-snapshot
agent-browser click @e1              # Use new refs
```

## Annotated Screenshots (Vision Mode)

Use `--annotate` to take a screenshot with numbered labels overlaid on interactive elements. Each label `[N]` maps to ref `@eN`. This also caches refs, so you can interact with elements immediately without a separate snapshot.

```bash
agent-browser screenshot --annotate
# Output includes the image path and a legend:
#   [1] @e1 button "Submit"
#   [2] @e2 link "Home"
#   [3] @e3 textbox "Email"
agent-browser click @e2              # Click using ref from annotated screenshot
```

Use annotated screenshots when:
- The page has unlabeled icon buttons or visual-only elements
- You need to verify visual layout or styling
- Canvas or chart elements are present (invisible to text snapshots)
- You need spatial reasoning about element positions

## Semantic Locators (Alternative to Refs)

When refs are unavailable or unreliable, use semantic locators:

```bash
agent-browser find text "Sign In" click
agent-browser find label "Email" fill "user@test.com"
agent-browser find role button click --name "Submit"
agent-browser find placeholder "Search" type "query"
agent-browser find testid "submit-btn" click
```

## JavaScript Evaluation (eval)

Use `eval` to run JavaScript in the browser context. **Shell quoting can corrupt complex expressions** -- use `--stdin` or `-b` to avoid issues.

```bash
# Simple expressions work with regular quoting
agent-browser eval 'document.title'
agent-browser eval 'document.querySelectorAll("img").length'

# Complex JS: use --stdin with heredoc (RECOMMENDED)
agent-browser eval --stdin <<'EVALEOF'
JSON.stringify(
  Array.from(document.querySelectorAll("img"))
    .filter(i => !i.alt)
    .map(i => ({ src: i.src.split("/").pop(), width: i.width }))
)
EVALEOF

# Alternative: base64 encoding (avoids all shell escaping issues)
agent-browser eval -b "$(echo -n 'Array.from(document.querySelectorAll("a")).map(a => a.href)' | base64)"
```

**Why this matters:** When the shell processes your command, inner double quotes, `!` characters (history expansion), backticks, and `$()` can all corrupt the JavaScript before it reaches agent-browser. The `--stdin` and `-b` flags bypass shell interpretation entirely.

**Rules of thumb:**
- Single-line, no nested quotes -> regular `eval 'expression'` with single quotes is fine
- Nested quotes, arrow functions, template literals, or multiline -> use `eval --stdin <<'EVALEOF'`
- Programmatic/generated scripts -> use `eval -b` with base64

## Configuration File

Create `agent-browser.json` in the project root for persistent settings:

```json
{
  "headed": true,
  "proxy": "http://localhost:8080",
  "profile": "./browser-data"
}
```

Priority (lowest to highest): `~/.agent-browser/config.json` < `./agent-browser.json` < env vars < CLI flags. Use `--config <path>` or `AGENT_BROWSER_CONFIG` env var for a custom config file (exits with error if missing/invalid). All CLI options map to camelCase keys (e.g., `--executable-path` -> `"executablePath"`). Boolean flags accept `true`/`false` values (e.g., `--headed false` overrides config). Extensions from user and project configs are merged, not replaced.

## Deep-Dive Documentation

| Reference | When to Use |
|-----------|-------------|
| [references/commands.md](references/commands.md) | Full command reference with all options |
| [references/snapshot-refs.md](references/snapshot-refs.md) | Ref lifecycle, invalidation rules, troubleshooting |
| [references/session-management.md](references/session-management.md) | Parallel sessions, state persistence, concurrent scraping |
| [references/authentication.md](references/authentication.md) | Login flows, OAuth, 2FA handling, state reuse |
| [references/video-recording.md](references/video-recording.md) | Recording workflows for debugging and documentation |
| [references/profiling.md](references/profiling.md) | Chrome DevTools profiling for performance analysis |
| [references/proxy-support.md](references/proxy-support.md) | Proxy configuration, geo-testing, rotating proxies |

## Experimental: Native Mode

agent-browser has an experimental native Rust daemon that communicates with Chrome directly via CDP, bypassing Node.js and Playwright entirely. It is opt-in and not recommended for production use yet.

```bash
# Enable via flag
agent-browser --native open example.com && agent-browser --native tab 0

# Enable via environment variable (avoids passing --native every time)
export AGENT_BROWSER_NATIVE=1
agent-browser open example.com && agent-browser tab 0
```

The native daemon supports Chromium and Safari (via WebDriver). Firefox and WebKit are not yet supported. All core commands (navigate, snapshot, click, fill, screenshot, cookies, storage, tabs, eval, etc.) work identically in native mode. Use `agent-browser close` before switching between native and default mode within the same session.

## Browser Engine Selection

Use `--engine` to choose a local browser engine. The default is `chrome`.

```bash
# Use Lightpanda (fast headless browser, requires separate install)
agent-browser --engine lightpanda open example.com && agent-browser --engine lightpanda tab 0

# Via environment variable
export AGENT_BROWSER_ENGINE=lightpanda
agent-browser open example.com && agent-browser tab 0

# With custom binary path
agent-browser --engine lightpanda --executable-path /path/to/lightpanda open example.com && agent-browser --engine lightpanda --executable-path /path/to/lightpanda tab 0
```

Supported engines:
- `chrome` (default) -- Chrome/Chromium via CDP
- `lightpanda` -- Lightpanda headless browser via CDP (10x faster, 10x less memory than Chrome)

Lightpanda does not support `--extension`, `--profile`, `--state`, or `--allow-file-access`. Install Lightpanda from https://lightpanda.io/docs/open-source/installation.

## Ready-to-Use Templates

| Template | Description |
|----------|-------------|
| [templates/form-automation.sh](templates/form-automation.sh) | Form filling with validation |
| [templates/capture-workflow.sh](templates/capture-workflow.sh) | Content extraction with screenshots |

```bash
./templates/form-automation.sh https://example.com/form
./templates/capture-workflow.sh https://example.com ./output
```
