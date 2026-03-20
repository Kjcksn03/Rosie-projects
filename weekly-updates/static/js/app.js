// Auto-dismiss alerts after 5s
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.alert').forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity .4s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 400);
    }, 5000);
  });

  // Status radio buttons — visual highlight
  document.querySelectorAll('.status-option input[type="radio"]').forEach(function (radio) {
    radio.addEventListener('change', function () {
      document.querySelectorAll('.status-option').forEach(function (opt) {
        opt.classList.remove('selected');
      });
      radio.closest('.status-option').classList.add('selected');
    });
  });
});
