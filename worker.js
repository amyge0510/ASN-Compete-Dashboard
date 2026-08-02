/**
 * Refresh-trigger proxy for the compete dashboard.
 *
 * The dashboard's "Refresh data" button POSTs here; this worker holds the
 * GitHub token server-side and triggers the monitor workflow — the visitor
 * never sees GitHub. Stateless by design: "already running" and the cooldown
 * are both derived from GitHub's own run list, so no KV/storage is needed.
 *
 * Deploy (browser only, ~5 minutes — no CLI):
 *   1. dash.cloudflare.com -> Workers & Pages -> Create -> "Hello World"
 *      -> name it (e.g. compete-refresh) -> Deploy -> Edit code -> paste
 *      this file -> Deploy.
 *   2. Worker -> Settings -> Variables and Secrets:
 *        Secret GITHUB_TOKEN  — fine-grained PAT scoped to ONLY the
 *          monitor repo, permissions: Actions (Read and write) +
 *          Metadata (Read-only). Note the expiry date you pick; the
 *          button dies quietly when the token expires.
 *        (Optional Text vars to override the defaults below:
 *         ALLOWED_ORIGIN, OWNER, REPO, WORKFLOW, COOLDOWN_MINUTES.)
 *   3. Set the repo variable REFRESH_HOOK_URL to the worker URL
 *      (https://<name>.<subdomain>.workers.dev) so the site renders the
 *      interactive button on its next publish.
 *
 * Responses are a fixed enum — GitHub response bodies are never echoed:
 *   {status: "started" | "already_running" | "cooling_down" | "error"}
 */

const DEFAULTS = {
  ALLOWED_ORIGIN: "https://neoapollo18.github.io",
  OWNER: "neoapollo18",
  REPO: "microsoft-web-scraper",
  WORKFLOW: "monitor.yml",
  COOLDOWN_MINUTES: "15",
};

// Anything not finished counts as active — during GitHub's queued window a
// second dispatch would be silently coalesced, so report it as running.
const ACTIVE_STATUSES = new Set(
  ["queued", "in_progress", "requested", "waiting", "pending"]);

export default {
  async fetch(request, env) {
    const cfg = (key) => (env && env[key]) || DEFAULTS[key];
    const cors = {
      "Access-Control-Allow-Origin": cfg("ALLOWED_ORIGIN"),
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
      "Cache-Control": "no-store",
    };
    const reply = (body, status = 200) =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json", ...cors },
      });

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }
    if (request.method !== "POST") {
      return reply({ status: "error" }, 405);
    }
    // Browser calls carry Origin; require it to match. (curl can fake it —
    // this is defense-in-depth, not the security boundary; the cooldown and
    // GitHub's concurrency group bound the blast radius.)
    const origin = request.headers.get("Origin");
    if (origin && origin !== cfg("ALLOWED_ORIGIN")) {
      return reply({ status: "error" }, 403);
    }

    const gh = (path, init = {}) =>
      fetch(`https://api.github.com/repos/${cfg("OWNER")}/${cfg("REPO")}${path}`, {
        ...init,
        headers: {
          // GitHub rejects requests without a User-Agent.
          "User-Agent": "compete-dashboard-refresh",
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          ...init.headers,
        },
      });

    if (!env || !env.GITHUB_TOKEN) {
      // Misconfigured deployment (secret not set) — distinct from GitHub
      // being unreachable so the operator can tell them apart.
      console.log("GITHUB_TOKEN secret is not configured");
      return reply({ status: "error" }, 500);
    }

    try {
      // One read answers both guards: active run? recent run (cooldown)?
      const runsResp = await gh(
        `/actions/workflows/${cfg("WORKFLOW")}/runs?per_page=5`);
      if (!runsResp.ok) {
        console.log("runs check failed:", runsResp.status);
        return reply({ status: "error" }, 502);
      }
      const runs = (await runsResp.json()).workflow_runs || [];
      if (runs.some((r) => ACTIVE_STATUSES.has(r.status))) {
        return reply({ status: "already_running" });
      }
      const cooldownMs = Number(cfg("COOLDOWN_MINUTES")) * 60 * 1000;
      const newest = runs[0]; // list is newest-first
      if (newest && Date.now() - Date.parse(newest.created_at) < cooldownMs) {
        return reply({ status: "cooling_down" });
      }

      // mode is hardcoded server-side: a spammer must never reach the
      // expensive backfill path.
      const dispatch = await gh(
        `/actions/workflows/${cfg("WORKFLOW")}/dispatches`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ref: "main", inputs: { mode: "monitor" } }),
        });
      if (dispatch.status === 204) {
        return reply({ status: "started" });
      }
      console.log("dispatch failed:", dispatch.status);
      return reply({ status: "error" }, 502);
    } catch (e) {
      console.log("worker error:", e && e.name);
      return reply({ status: "error" }, 502);
    }
  },
};
