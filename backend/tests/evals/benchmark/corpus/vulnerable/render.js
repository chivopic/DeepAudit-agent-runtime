// Comment rendering for the activity feed.
export function renderComment(el, comment) {
  el.innerHTML = "<p>" + comment.body + "</p>";
}

export function runHook(el, source) {
  el.setAttribute("onclick", source);
}
