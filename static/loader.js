document.addEventListener("DOMContentLoaded", () => {
    const form = document.getElementById("uploadForm");
    const loader = document.getElementById("loader");

    form.addEventListener("submit", () => {
        loader.classList.remove("hidden");
    });
});
