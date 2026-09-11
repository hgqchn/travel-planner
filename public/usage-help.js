"use strict";

(() => {
  const dialog = document.getElementById("usage-help-dialog");
  let opener = null;
  document.querySelectorAll("[data-help-open]").forEach((button) => {
    button.addEventListener("click", () => {
      if (dialog.open) return;
      opener = button;
      dialog.showModal();
      dialog.querySelector(".usage-help-content").scrollTop = 0;
    });
  });
  dialog.querySelectorAll("[data-help-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("close", () => {
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    opener = null;
  });
})();
