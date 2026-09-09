/**
 * Silent voting API for the Community Game submissions site.
 *
 * - Anyone can cast one vote per post, and remove it later.
 * - Nobody (including the site) can see how many votes a post has through
 *   the public API — only /api/mine (which posts THIS visitor voted for)
 *   is exposed. Totals are only readable via /api/results with the admin
 *   secret, for revealing results once voting closes.
 *
 * KV bindings expected (set in wrangler.toml):
 *   VOTES        - stores vote records + counts
 * Secrets expected (wrangler secret put ...):
 *   SYNC_SECRET  - shared with the Discord bot, authorizes /api/sync
 *   ADMIN_SECRET - authorizes /api/results
 * Vars expected (wrangler.toml [vars]):
 *   ALLOWED_ORIGIN - e.g. https://yourname.github.io
 */

function corsHeaders(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,X-Sync-Secret,X-Admin-Secret",
  };
}

function json(data, status, env) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(env) },
  });
}

async function isValidPostId(env, postId) {
  const raw = await env.VOTES.get("validIds");
  if (!raw) return true; // haven't synced yet, don't block everything
  const ids = JSON.parse(raw);
  return ids.includes(postId);
}

async function handleVote(request, env, remove) {
  const body = await request.json().catch(() => ({}));
  const { voterId, postId } = body;

  if (!voterId || !postId || typeof voterId !== "string" || typeof postId !== "string") {
    return json({ error: "voterId and postId are required" }, 400, env);
  }
  if (!(await isValidPostId(env, postId))) {
    return json({ error: "unknown postId" }, 404, env);
  }

  const voteKey = `vote:${voterId}:${postId}`;
  const countKey = `count:${postId}`;
  const existing = await env.VOTES.get(voteKey);

  if (remove) {
    if (existing) {
      await env.VOTES.delete(voteKey);
      const current = parseInt((await env.VOTES.get(countKey)) || "0", 10);
      await env.VOTES.put(countKey, String(Math.max(0, current - 1)));
    }
    return json({ voted: false }, 200, env);
  }

  if (!existing) {
    await env.VOTES.put(voteKey, "1");
    const current = parseInt((await env.VOTES.get(countKey)) || "0", 10);
    await env.VOTES.put(countKey, String(current + 1));
  }
  return json({ voted: true }, 200, env);
}

async function handleMine(request, env) {
  const url = new URL(request.url);
  const voterId = url.searchParams.get("voterId");
  if (!voterId) return json({ error: "voterId is required" }, 400, env);

  const list = await env.VOTES.list({ prefix: `vote:${voterId}:` });
  const postIds = list.keys.map((k) => k.name.slice(`vote:${voterId}:`.length));
  return json({ voted: postIds }, 200, env);
}

async function handleSync(request, env) {
  const secret = request.headers.get("X-Sync-Secret");
  if (!secret || secret !== env.SYNC_SECRET) {
    return json({ error: "unauthorized" }, 401, env);
  }
  const body = await request.json().catch(() => ({}));
  const ids = Array.isArray(body.ids) ? body.ids : [];
  await env.VOTES.put("validIds", JSON.stringify(ids));
  return json({ synced: ids.length }, 200, env);
}

async function handleResults(request, env) {
  const secret = request.headers.get("X-Admin-Secret");
  if (!secret || secret !== env.ADMIN_SECRET) {
    return json({ error: "unauthorized" }, 401, env);
  }
  const raw = await env.VOTES.get("validIds");
  const ids = raw ? JSON.parse(raw) : [];
  const results = {};
  for (const id of ids) {
    results[id] = parseInt((await env.VOTES.get(`count:${id}`)) || "0", 10);
  }
  return json({ results }, 200, env);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders(env) });
    }

    if (url.pathname === "/api/vote" && request.method === "POST") {
      return handleVote(request, env, false);
    }
    if (url.pathname === "/api/unvote" && request.method === "POST") {
      return handleVote(request, env, true);
    }
    if (url.pathname === "/api/mine" && request.method === "GET") {
      return handleMine(request, env);
    }
    if (url.pathname === "/api/sync" && request.method === "POST") {
      return handleSync(request, env);
    }
    if (url.pathname === "/api/results" && request.method === "GET") {
      return handleResults(request, env);
    }

    return json({ error: "not found" }, 404, env);
  },
};
