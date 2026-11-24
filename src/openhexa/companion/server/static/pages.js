// Progressive enhancements of the companion's pages, which all work without JavaScript.

document.addEventListener("DOMContentLoaded", () => {
  // Forms with several workspace rows: add and remove rows.
  for (const form of document.querySelectorAll("[data-workspace-rows]")) {
    const rows = form.querySelector("[data-rows]");
    const template = form.querySelector("[data-row-template]");
    const add = form.querySelector("[data-add-row]");
    const showRemoveButtons = () => {
      for (const button of rows.querySelectorAll("[data-remove-row]")) {
        button.hidden = false;
      }
    };
    showRemoveButtons();
    add.hidden = false;
    add.addEventListener("click", () => {
      rows.appendChild(template.content.cloneNode(true));
      showRemoveButtons();
      rows.lastElementChild.querySelector("input").focus();
    });
    rows.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove-row]");
      if (remove) {
        remove.closest(".workspace-row").remove();
        add.focus();
      }
    });
  }

  // Copy buttons, for the text of the element they name.
  for (const button of document.querySelectorAll("[data-copy]")) {
    if (!navigator.clipboard) {
      continue;
    }
    button.hidden = false;
    button.addEventListener("click", async () => {
      const text = document.getElementById(button.dataset.copy).textContent;
      await navigator.clipboard.writeText(text);
      button.textContent = "Copied";
      setTimeout(() => {
        button.textContent = "Copy";
      }, 2000);
    });
  }

  // Ask before destructive actions.
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-confirm]");
    if (button && !window.confirm(button.dataset.confirm)) {
      event.preventDefault();
    }
  });
});
