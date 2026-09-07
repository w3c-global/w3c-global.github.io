"use strict";
const topButton = document.querySelector(".to-top");
if (topButton) {
  window.addEventListener(
    "scroll",
    () => {
      topButton.hidden = window.scrollY < 600;
    },
    { passive: true },
  );
  topButton.addEventListener("click", () =>
    window.scrollTo({
      top: 0,
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    }),
  );
}
const form = document.querySelector("#contact-form");
if (form)
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const v = Object.fromEntries(new FormData(form));
    if (v.website) return;
    const body = `Name: ${v.name}\nWork email: ${v.email}\nOrganisation: ${v.organisation || "Not specified"}\n\n${v.message}`;
    window.location.href = `mailto:frankie@w3c.com?subject=${encodeURIComponent("Agentu enquiry")}&body=${encodeURIComponent(body)}`;
    document.querySelector("#contact-status").textContent =
      "Your email application should open with the enquiry filled in. Review and send it there. If it does not open, email frankie@w3c.com. Nothing has been submitted by this website.";
  });
