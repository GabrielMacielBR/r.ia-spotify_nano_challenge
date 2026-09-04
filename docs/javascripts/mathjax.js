window.MathJax = {
  tex: {
    inlineMath: [["\\(", "\\)"]],
    displayMath: [["\\[", "\\]"]],
    processEscapes: true,
    processEnvironments: true
  },
  options: {
    ignoreHtmlClass: ".*|",
    processHtmlClass: "arithmatex"
  }
};

document$.subscribe(() => {
  if (typeof MathJax !== "undefined") {
    if (MathJax.startup && MathJax.startup.promise) {
      MathJax.startup.promise.then(() => {
        MathJax.typesetClear();
        MathJax.texReset();
        MathJax.typesetPromise();
      }).catch((err) => console.error("MathJax error:", err));
    } else if (typeof MathJax.typesetPromise === "function") {
      MathJax.typesetClear();
      MathJax.texReset();
      MathJax.typesetPromise().catch((err) => console.error("MathJax error:", err));
    }
  }
});
