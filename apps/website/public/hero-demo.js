/* 0xCopilot — interactive hero demo (vanilla, no deps).
   The widget previews live under a "Try it live" scrim; click to interact,
   then switch modes + scrub the 4-lane timeline. */
(function () {
  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }
  ready(function () {
    var tm = document.getElementById("tmWidget");
    if (!tm) return;
    var play = document.getElementById("tmPlay");
    var img = document.getElementById("tmImg");
    var LIVE = 80,
      lastIdx = 0;

    /* modes */
    var modeBtns = tm.querySelectorAll(".tm-modes button");
    modeBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        tm.dataset.mode = b.dataset.mode;
        modeBtns.forEach(function (x) {
          x.classList.toggle("on", x === b);
        });
      });
    });

    /* timeline */
    var track = document.getElementById("tmTrack");
    var head = document.getElementById("tmHead");
    var clock = document.getElementById("tmClock");
    var subEl = document.getElementById("tmSub");
    var lbl = document.getElementById("tmLbl");
    var banner = document.getElementById("tmBanner");
    var beadEls = Array.prototype.slice.call(
      track.querySelectorAll(".tm-bead"),
    );
    var data = beadEls
      .map(function (el) {
        return {
          el: el,
          x: +el.dataset.x,
          lane: +el.dataset.lane,
          t: el.dataset.t,
          app: el.dataset.app,
          short: el.dataset.short,
          live: el.dataset.live === "1",
        };
      })
      .sort(function (a, b) {
        return a.x - b.x;
      });

    function layout() {
      data.forEach(function (d) {
        d.el.style.left = d.x + "%";
        d.el.style.top = ((d.lane + 0.5) / 4) * 100 + "%";
      });
    }

    function setHead(p) {
      p = Math.max(4, Math.min(96, p));
      head.style.left = p + "%";
      var live = p >= LIVE - 1;
      var cur = data[0],
        idx = 0;
      data.forEach(function (d, i) {
        if (d.x <= p + 0.5) {
          cur = d;
          idx = i;
        }
      });
      lastIdx = idx;
      data.forEach(function (d) {
        d.el.className = "tm-bead l" + d.lane;
        if (d.live && live) d.el.classList.add("now");
        else if (d.x > p + 0.5) d.el.classList.add("future");
        else d.el.classList.add("done");
        if (d === cur && !live) d.el.classList.add("cur");
      });
      if (live) {
        tm.classList.remove("scrubbing");
        lbl.textContent = "LIVE";
        lbl.className = "lbl live";
        clock.textContent = "11:44:00";
        clock.classList.remove("view");
        subEl.textContent = "X thread · Stream body · 64%";
      } else {
        tm.classList.add("scrubbing");
        lbl.textContent = "VIEWING";
        lbl.className = "lbl view";
        clock.textContent = cur.t + ":00";
        clock.classList.add("view");
        subEl.textContent = cur.app + " · " + cur.short;
        if (banner)
          banner.textContent =
            "Viewing " + cur.t + " · " + cur.app + " — " + cur.short;
      }
    }

    function pctOf(e) {
      var r = track.getBoundingClientRect();
      return ((e.clientX - r.left) / r.width) * 100;
    }
    function snapSet(p) {
      var near = null,
        dist = 99;
      data.forEach(function (d) {
        var dd = Math.abs(d.x - p);
        if (dd < dist) {
          dist = dd;
          near = d;
        }
      });
      setHead(near && dist < 4 ? near.x : p);
    }
    var dragging = false;
    track.addEventListener("pointerdown", function (e) {
      dragging = true;
      try {
        track.setPointerCapture(e.pointerId);
      } catch (_) {}
      snapSet(pctOf(e));
    });
    track.addEventListener("pointermove", function (e) {
      if (dragging) setHead(pctOf(e));
    });
    window.addEventListener("pointerup", function () {
      dragging = false;
    });

    var prev = document.getElementById("tmPrev"),
      next = document.getElementById("tmNow2"),
      now = document.getElementById("tmNow");
    if (prev)
      prev.addEventListener("click", function () {
        setHead(data[Math.max(0, lastIdx - 1)].x);
      });
    if (next)
      next.addEventListener("click", function () {
        setHead(data[Math.min(data.length - 1, lastIdx + 1)].x);
      });
    if (now)
      now.addEventListener("click", function () {
        setHead(LIVE);
      });

    /* activation — reveal full interaction */
    function activate() {
      if (play) play.style.display = "none";
      if (img) img.style.display = "none";
    }
    if (play) play.addEventListener("click", activate);

    /* init: position beads + set LIVE so the resting preview is correct */
    layout();
    setHead(LIVE);
  });
})();
