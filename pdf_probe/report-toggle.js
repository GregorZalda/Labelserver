document.getElementById('ovl-toggle').addEventListener('change', function (e) {
  document.querySelectorAll('.ovl').forEach(function (b) {
    b.style.display = e.target.checked ? 'block' : 'none';
  });
});
