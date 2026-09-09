// ---- Configuration -------------------------------------------------------
// Fill in your deployed Worker URL after `wrangler deploy` (no trailing slash).
const CONFIG = {
  WORKER_URL: "https://community-game-votes.tako-spiel.workers.dev",
  DATA_URL: "data/submissions.json",
};

const CATEGORY_ORDER = ["Main", "Details", "Design", "User", "UI", "Uncategorized"];

const CATEGORY_INFO = {
  Main: { color: "var(--c-main)", blurb: "Core pitches for what the game fundamentally is." },
  Details: { color: "var(--c-details)", blurb: "Specific mechanics, systems, and world details." },
  Design: { color: "var(--c-design)", blurb: "Visual style, art direction, and design language." },
  User: { color: "var(--c-user)", blurb: "Player experience, onboarding, and accessibility." },
  UI: { color: "var(--c-ui)", blurb: "Interface layout, HUD, and menu ideas." },
  Uncategorized: { color: "var(--c-uncategorized)", blurb: "Missing a C: tag — take a look and re-tag on Discord." },
};

// ---- Voter identity (anonymous, local to this browser) -------------------

function getVoterId() {
  let id = localStorage.getItem("voterId");
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem("voterId", id);
  }
  return id;
}

// ---- Helpers ---------------------------------------------------------------

function shuffle(arr) {
  const a = arr.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function groupByCategory(posts) {
  const groups = {};
  for (const post of posts) {
    const cat = CATEGORY_INFO[post.category] ? post.category : "Uncategorized";
    (groups[cat] ||= []).push(post);
  }
  return groups;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str ?? "";
  return div.innerHTML;
}

// ---- Rendering ---------------------------------------------------------------

function renderNav(groups) {
  const nav = document.getElementById("category-nav");
  nav.innerHTML = "";
  for (const cat of CATEGORY_ORDER) {
    if (!groups[cat] || groups[cat].length === 0) continue;
    const a = document.createElement("a");
    a.href = `#cat-${cat}`;
    a.textContent = `${cat} (${groups[cat].length})`;
    a.style.setProperty("--dot", CATEGORY_INFO[cat].color);
    nav.appendChild(a);
  }
}

function postCard(post, votedSet) {
  const card = document.createElement("article");
  card.className = "post-card";

  const title = document.createElement("h3");
  title.className = "post-title";
  title.textContent = post.title || "(untitled)";
  card.appendChild(title);

  if (post.content && post.content.trim() !== post.title?.trim()) {
    const body = document.createElement("p");
    body.className = "post-content";
    body.textContent = post.content;
    card.appendChild(body);
  }

  const meta = document.createElement("div");
  meta.className = "post-meta";

  const author = document.createElement("span");
  author.className = "post-author";
  author.textContent = `@${post.author}`;
  meta.appendChild(author);

  const btn = document.createElement("button");
  btn.className = "vote-btn";
  const voted = votedSet.has(post.id);
  btn.dataset.voted = String(voted);
  btn.textContent = voted ? "Voted ✓" : "Vote";
  btn.addEventListener("click", () => toggleVote(post.id, btn, votedSet));
  meta.appendChild(btn);

  card.appendChild(meta);
  return card;
}

function renderBoard(groups, votedSet) {
  const board = document.getElementById("board");
  board.innerHTML = "";

  const anyPosts = CATEGORY_ORDER.some((c) => groups[c]?.length);
  if (!anyPosts) {
    board.innerHTML = '<p class="empty">No submissions yet — post in #Submissions on Discord.</p>';
    return;
  }

  for (const cat of CATEGORY_ORDER) {
    const posts = groups[cat];
    if (!posts || posts.length === 0) continue;

    const section = document.createElement("section");
    section.className = "category-section";
    section.id = `cat-${cat}`;

    const heading = document.createElement("div");
    heading.className = "category-heading";
    heading.style.setProperty("--dot", CATEGORY_INFO[cat].color);
    heading.innerHTML = `
      <span class="category-dot"></span>
      <h2>${escapeHtml(cat)}</h2>
      <span class="category-count">${posts.length}</span>
    `;
    section.appendChild(heading);

    const blurb = document.createElement("p");
    blurb.className = "category-blurb";
    blurb.textContent = CATEGORY_INFO[cat].blurb;
    section.appendChild(blurb);

    const grid = document.createElement("div");
    grid.className = "post-grid";
    grid.style.setProperty("--dot", CATEGORY_INFO[cat].color);
    for (const post of shuffle(posts)) {
      grid.appendChild(postCard(post, votedSet));
    }
    section.appendChild(grid);

    board.appendChild(section);
  }
}

// ---- Voting --------------------------------------------------------------

async function toggleVote(postId, btn, votedSet) {
  const voterId = getVoterId();
  const currentlyVoted = votedSet.has(postId);
  const endpoint = currentlyVoted ? "/api/unvote" : "/api/vote";

  btn.disabled = true;
  try {
    const res = await fetch(`${CONFIG.WORKER_URL}${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voterId, postId }),
    });
    if (!res.ok) throw new Error(`Worker responded ${res.status}`);

    if (currentlyVoted) {
      votedSet.delete(postId);
      btn.dataset.voted = "false";
      btn.textContent = "Vote";
    } else {
      votedSet.add(postId);
      btn.dataset.voted = "true";
      btn.textContent = "Voted ✓";
    }
  } catch (err) {
    console.error("Vote failed:", err);
    btn.textContent = "Try again";
  } finally {
    btn.disabled = false;
  }
}

async function fetchVotedSet() {
  try {
    const voterId = getVoterId();
    const res = await fetch(`${CONFIG.WORKER_URL}/api/mine?voterId=${voterId}`);
    if (!res.ok) throw new Error(`Worker responded ${res.status}`);
    const data = await res.json();
    return new Set(data.voted || []);
  } catch (err) {
    console.warn("Could not load vote status (voting still works, just won't show state):", err);
    return new Set();
  }
}

// ---- Boot ------------------------------------------------------------------

async function init() {
  const loading = document.getElementById("loading");
  try {
    const [postsRes, votedSet] = await Promise.all([
      fetch(CONFIG.DATA_URL).then((r) => {
        if (!r.ok) throw new Error(`submissions.json responded ${r.status}`);
        return r.json();
      }),
      fetchVotedSet(),
    ]);

    const groups = groupByCategory(postsRes);
    renderNav(groups);
    renderBoard(groups, votedSet);
  } catch (err) {
    console.error(err);
    document.getElementById("board").innerHTML =
      '<p class="error">Couldn\'t load submissions. Check back in a bit.</p>';
  } finally {
    loading?.remove();
  }
}

init();
