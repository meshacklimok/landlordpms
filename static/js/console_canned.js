// Saved replies: a click puts the text in the reply box, ready to change before sending.
document.addEventListener("click", function (event) {
  var button = event.target.closest("[data-canned]");
  if (!button) return;
  var box = document.getElementById(button.getAttribute("data-canned"));
  if (!box) return;
  box.value = (box.value ? box.value.replace(/\s+$/, "") + "\n\n" : "") + button.getAttribute("data-text");
  box.focus();
});
