(function () {
  const wrap = document.querySelector(".article-wrap");
  if (!wrap) return;
  const content = document.getElementById("article-content");
  const saveUrl = wrap.dataset.saveUrl;
  const status = document.getElementById("save-status");
  const btnHl = document.getElementById("btn-highlight");
  const btnUn = document.getElementById("btn-unhighlight");
  const btnSave = document.getElementById("btn-save");
  let dirty = false;
  let saveTimer = null;

  function setStatus(msg, kind) {
    if (!status) return;
    status.textContent = msg || "";
    status.style.color = kind === "err" ? "var(--danger)" : "var(--muted)";
  }

  function markDirty() {
    dirty = true;
    setStatus("Modifiche non salvate…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 1200);
  }

  async function save() {
    if (!dirty) return;
    try {
      setStatus("Salvataggio…");
      const res = await fetch(saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ html: content.innerHTML }),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      dirty = false;
      const now = new Date();
      setStatus("Salvato " + now.toLocaleTimeString());
    } catch (e) {
      setStatus("Errore salvataggio: " + e.message, "err");
    }
  }

  // Wrap selected text in a <mark class="user-hl">
  function highlightSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) return;
    const range = sel.getRangeAt(0);
    if (!content.contains(range.commonAncestorContainer)) return;

    // For a single text node selection: simple wrap
    if (range.startContainer === range.endContainer &&
        range.startContainer.nodeType === Node.TEXT_NODE) {
      const mark = document.createElement("mark");
      mark.className = "user-hl";
      try {
        range.surroundContents(mark);
        sel.removeAllRanges();
        markDirty();
        return;
      } catch (_) { /* fall through */ }
    }

    // For multi-node selections: walk text nodes inside the range and wrap them
    const root = range.commonAncestorContainer;
    const walker = document.createTreeWalker(
      root.nodeType === Node.TEXT_NODE ? root.parentNode : root,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode(node) {
          if (!range.intersectsNode(node)) return NodeFilter.FILTER_REJECT;
          // Skip text that's entirely outside the range
          const r = document.createRange();
          r.selectNodeContents(node);
          if (range.compareBoundaryPoints(Range.END_TO_START, r) >= 0) return NodeFilter.FILTER_REJECT;
          if (range.compareBoundaryPoints(Range.START_TO_END, r) <= 0) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      }
    );
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) nodes.push(n);

    nodes.forEach((node) => {
      let start = 0, end = node.nodeValue.length;
      if (node === range.startContainer) start = range.startOffset;
      if (node === range.endContainer) end = range.endOffset;
      if (start >= end) return;

      const text = node.nodeValue;
      const before = text.slice(0, start);
      const middle = text.slice(start, end);
      const after = text.slice(end);
      if (!middle.trim()) return;

      const frag = document.createDocumentFragment();
      if (before) frag.appendChild(document.createTextNode(before));
      const mark = document.createElement("mark");
      mark.className = "user-hl";
      mark.appendChild(document.createTextNode(middle));
      frag.appendChild(mark);
      if (after) frag.appendChild(document.createTextNode(after));
      node.parentNode.replaceChild(frag, node);
    });

    sel.removeAllRanges();
    markDirty();
  }

  // Remove any <mark.user-hl> intersecting the selection
  function unhighlightSelection() {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0) return;
    const range = sel.getRangeAt(0);

    // Find all marks in the article and unwrap those touched by the range
    const marks = Array.from(content.querySelectorAll("mark.user-hl"));
    let changed = false;
    marks.forEach((m) => {
      if (sel.isCollapsed) {
        // If the caret is inside this mark, remove the whole mark
        if (m.contains(range.startContainer)) {
          unwrap(m);
          changed = true;
        }
      } else if (range.intersectsNode(m)) {
        unwrap(m);
        changed = true;
      }
    });
    if (changed) {
      sel.removeAllRanges();
      markDirty();
    }
  }

  function unwrap(el) {
    const parent = el.parentNode;
    while (el.firstChild) parent.insertBefore(el.firstChild, el);
    parent.removeChild(el);
    parent.normalize();
  }

  if (btnHl) btnHl.addEventListener("click", highlightSelection);
  if (btnUn) btnUn.addEventListener("click", unhighlightSelection);
  if (btnSave) btnSave.addEventListener("click", () => { dirty = true; save(); });

  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    if (e.key === "s" || e.key === "S") { if (!e.metaKey && !e.ctrlKey) { e.preventDefault(); highlightSelection(); } }
    if (e.key === "u" || e.key === "U") { if (!e.metaKey && !e.ctrlKey) { e.preventDefault(); unhighlightSelection(); } }
    if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); dirty = true; save(); }
  });

  // Auto-save on unload if dirty
  window.addEventListener("beforeunload", () => {
    if (dirty) {
      navigator.sendBeacon(
        saveUrl,
        new Blob([JSON.stringify({ html: content.innerHTML })], { type: "application/json" })
      );
    }
  });
})();
