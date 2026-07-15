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
  }

  document.addEventListener('DOMContentLoaded', initStats);
})();
