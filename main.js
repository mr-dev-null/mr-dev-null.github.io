(function () {
    "use strict";

    /* ── Sticky nav border ── */
    const nav = document.querySelector("nav:not(.article-nav)");
    if (nav) {
        const onScroll = () => nav.classList.toggle("scrolled", window.scrollY > 10);
        window.addEventListener("scroll", onScroll, { passive: true });
        onScroll();
    }

    /* ── Mobile nav toggle ── */
    const toggle = document.querySelector(".nav-toggle");
    const navLinks = document.querySelector(".nav-links");
    if (toggle && navLinks) {
        toggle.addEventListener("click", () => {
            const open = navLinks.classList.toggle("open");
            toggle.textContent = open ? "[close]" : "[menu]";
            toggle.setAttribute("aria-expanded", open);
        });
    }

    /* ── Reading progress bar ── */
    const bar = document.getElementById("reading-progress");
    if (bar) {
        bar.classList.add("visible");
        const updateBar = () => {
            const doc = document.documentElement;
            const total = doc.scrollHeight - doc.clientHeight;
            const pct = total > 0 ? (window.scrollY / total) * 100 : 0;
            bar.style.width = Math.min(100, pct).toFixed(2) + "%";
        };
        window.addEventListener("scroll", updateBar, { passive: true });
        updateBar();
    }

    /* ── Copy buttons ── */
    const COPY_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`;
    const CHECK_ICON = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;

    document.querySelectorAll(".copy-btn").forEach((btn) => {
        btn.innerHTML = COPY_ICON + "copy";
        btn.addEventListener("click", () => {
            const wrapper = btn.closest(".code-wrapper");
            const code = wrapper ? wrapper.querySelector("code") : null;
            if (!code) return;

            const text = code.innerText;
            const done = () => {
                btn.innerHTML = CHECK_ICON + "copied";
                btn.style.color = "var(--accent)";
                btn.style.borderColor = "var(--accent)";
                setTimeout(() => {
                    btn.innerHTML = COPY_ICON + "copy";
                    btn.style.color = "";
                    btn.style.borderColor = "";
                }, 2000);
            };

            if (navigator.clipboard) {
                navigator.clipboard.writeText(text).then(done).catch(() => fallback(text, done));
            } else {
                fallback(text, done);
            }
        });
    });

    function fallback(text, done) {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand("copy"); done(); } catch (e) {}
        document.body.removeChild(ta);
    }

    /* ── Copyright year ── */
    document.querySelectorAll(".copyright-year").forEach((el) => {
        el.textContent = new Date().getFullYear();
    });
})();
