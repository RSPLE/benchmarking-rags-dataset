const button = document.querySelector("[data-password-toggle]");
const password = document.getElementById("password");

if (button && password) {
  button.addEventListener("click", () => {
    const showing = password.type === "text";
    password.type = showing ? "password" : "text";
    button.setAttribute("aria-pressed", String(!showing));
    button.setAttribute("aria-label", showing ? "Mostrar senha" : "Ocultar senha");
    button.textContent = showing ? "Mostrar" : "Ocultar";
    password.focus();
  });
}
