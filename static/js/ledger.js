document.addEventListener("DOMContentLoaded", () => {
  // Show the chosen file name in the upload control.
  document.querySelectorAll(".file-input input[type='file']").forEach((input) => {
    const label = input.closest(".file-input");
    const fileLabel = label?.querySelector("[data-file-label]");
    const placeholder = fileLabel?.textContent;
    input.addEventListener("change", () => {
      if (!fileLabel) return;
      fileLabel.textContent = input.files?.[0]?.name || placeholder;
      label.classList.toggle("has-file", Boolean(input.files?.length));
    });
  });

  // Account forms: show the manual password fields only when "set it myself" is chosen.
  document.querySelectorAll("[data-password-mode]").forEach((fieldset) => {
    const manual = fieldset.querySelector("[data-manual-password]");
    const sync = () => {
      const isManual = fieldset.querySelector("input[type='radio'][value='manual']")?.checked;
      if (manual) manual.hidden = !isManual;
    };
    fieldset.addEventListener("change", sync);
    sync();
  });

  // One-time password screen: copy to clipboard.
  document.querySelectorAll("[data-copy-secret]").forEach((button) => {
    button.addEventListener("click", async () => {
      const secret = document.querySelector("[data-secret]")?.textContent.trim();
      if (!secret) return;
      try {
        await navigator.clipboard.writeText(secret);
        button.textContent = "Tersalin ✓";
      } catch {
        const range = document.createRange();
        range.selectNodeContents(document.querySelector("[data-secret]"));
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
        button.textContent = "Tekan Ctrl+C";
      }
    });
  });

  // Ask before destructive or irreversible actions: <form data-confirm="...">.
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  // Prevent double submits (duplicate rows / journals) while a POST is in flight.
  document.querySelectorAll("form[method='post']").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (form.dataset.submitting) {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      form.querySelectorAll("button[type='submit']").forEach((button) => {
        button.setAttribute("aria-busy", "true");
      });
    });
  });
  // Pages restored from the back/forward cache must accept submits again.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll("form[data-submitting]").forEach((form) => {
      delete form.dataset.submitting;
      form.querySelectorAll("[aria-busy]").forEach((button) => button.removeAttribute("aria-busy"));
    });
  });
});
