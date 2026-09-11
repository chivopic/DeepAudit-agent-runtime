// Comment rendering for the activity feed.
export function renderComment(el, comment) {
  const p = document.createElement("p");
  p.textContent = comment.body;
  el.replaceChildren(p);
}

export function runHook(el, handler) {
  el.addEventListener("click", handler);
}
