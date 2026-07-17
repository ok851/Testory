(function () {
  'use strict';

  function animateCounter(el, target, suffix, duration) {
    suffix = suffix || '';
    duration = duration || 2000;
    var start = 0;
    var startTime = Date.now();
    function update() {
      var elapsed = Date.now() - startTime;
      var progress = Math.min(elapsed / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      el.textContent = Math.floor(start + (target - start) * eased).toLocaleString() + suffix;
      if (progress < 1) requestAnimationFrame(update);
    }
    requestAnimationFrame(update);
  }

  function initStats() {
    var stats = document.querySelectorAll('.ai-hub-stat__num');
    if (!stats.length) return;

    // 先从后端获取真实数据，再触发动画
    fetch('/api/ai/stats', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.success) {
          var targets = [
            d.ai_generated_cases || 0,
            d.efficiency_boost || 0,
            d.coverage_boost || 0
          ];
          stats.forEach(function (el, idx) {
            var target = targets[idx] || 0;
            el.dataset.target = String(target);
          });
        }
        // 数据更新后开始观察并动画
        var observer = new IntersectionObserver(function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              var el = entry.target;
              var target = parseInt(el.dataset.target, 10) || 0;
              var suffix = el.dataset.suffix || '';
              if (target > 0) animateCounter(el, target, suffix);
              observer.unobserve(el);
            }
          });
        }, { threshold: 0.3 });
        stats.forEach(function (el) { observer.observe(el); });
      })
      .catch(function () {
        // 失败时仍按页面上的默认值（0）执行动画，不中断体验
        var observer = new IntersectionObserver(function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              var el = entry.target;
              var target = parseInt(el.dataset.target, 10) || 0;
              var suffix = el.dataset.suffix || '';
              if (target > 0) animateCounter(el, target, suffix);
              observer.unobserve(el);
            }
          });
        }, { threshold: 0.3 });
        stats.forEach(function (el) { observer.observe(el); });
      });
  }

  function initSettings() {
    var btn = document.getElementById('aiHubSettingsBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (typeof window.toggleAiSettings === 'function') {
        window.toggleAiSettings(true);
      } else {
        var event = new CustomEvent('aiOpenSettings', { bubbles: true, cancelable: true });
        document.dispatchEvent(event);
        if (window.__testory && window.__testory.openAISettings) {
          window.__testory.openAISettings();
        }
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    initStats();
    initSettings();
  });
})();
