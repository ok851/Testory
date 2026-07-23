(function () {
  "use strict";

  const nav = document.querySelector(".site-nav");
  const toggle = document.querySelector(".nav-toggle");
  const navLinks = document.querySelector(".nav-links");

  function onScroll() {
    if (nav) nav.classList.toggle("scrolled", window.scrollY > 24);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  if (toggle && navLinks) {
    toggle.addEventListener("click", () => navLinks.classList.toggle("open"));
    navLinks.querySelectorAll("a").forEach((a) => {
      a.addEventListener("click", () => navLinks.classList.remove("open"));
    });
  }

  const reveals = document.querySelectorAll(".reveal");
  if (reveals.length && "IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (e.isIntersecting) {
            e.target.classList.add("visible");
            io.unobserve(e.target);
          }
        });
      },
      { threshold: 0.12, rootMargin: "0px 0px -40px 0px" }
    );
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("visible"));
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.tab;
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      const panel = document.getElementById(id);
      if (panel) panel.classList.add("active");
    });
  });

  document.querySelectorAll(".faq-q").forEach((btn) => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".faq-item");
      const wasOpen = item.classList.contains("open");
      document.querySelectorAll(".faq-item").forEach((i) => i.classList.remove("open"));
      if (!wasOpen) item.classList.add("open");
    });
  });

  const form = document.getElementById("contactForm");
  if (form) {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const msg = document.getElementById("formMsg");
      const btn = form.querySelector('button[type="submit"]');
      btn.disabled = true;
      msg.textContent = "";
      msg.className = "form-msg";
      try {
        const body = {
          name: form.name.value.trim(),
          email: form.email.value.trim(),
          company: form.company.value.trim(),
          message: form.message.value.trim(),
        };
        const r = await fetch("/api/contact", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const d = await r.json();
        if (d.success) {
          msg.textContent = d.message || "提交成功";
          msg.classList.add("success");
          form.reset();
        } else {
          msg.textContent = d.error || "提交失败";
          msg.classList.add("error");
        }
      } catch (err) {
        msg.textContent = "网络错误，请稍后重试";
        msg.classList.add("error");
      } finally {
        btn.disabled = false;
      }
    });
  }

  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    a.addEventListener("click", (e) => {
      const id = a.getAttribute("href");
      if (id.length <= 1) return;
      const el = document.querySelector(id);
      if (el) {
        e.preventDefault();
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  });

  // 访问统计：匿名 visitor_id + 当前 path
  try {
    const storageKey = "testory_vid";
    let visitorId = "";
    try {
      visitorId = localStorage.getItem(storageKey) || "";
    } catch (_) {}
    if (!visitorId || visitorId.length < 8) {
      visitorId =
        "v_" +
        Math.random().toString(36).slice(2) +
        Date.now().toString(36);
      try {
        localStorage.setItem(storageKey, visitorId);
      } catch (_) {}
    }
    const payload = JSON.stringify({
      visitor_id: visitorId,
      path: location.pathname + location.search,
      referrer: document.referrer || "",
      title: document.title || "",
    });
    if (navigator.sendBeacon) {
      const blob = new Blob([payload], { type: "application/json" });
      navigator.sendBeacon("/api/visit", blob);
    } else {
      fetch("/api/visit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: payload,
        keepalive: true,
      }).catch(() => {});
    }
  } catch (_) {}
})();
