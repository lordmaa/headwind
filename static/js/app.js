document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('.flash').forEach(function (el) {
    setTimeout(function () {
      el.style.transition = 'opacity 0.4s';
      el.style.opacity = '0';
      setTimeout(function () { el.remove(); }, 400);
    }, 4000);
  });
});

function doSync() {
  var btn = document.getElementById('syncBtn');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = 'Syncing...';
  fetch('/sync', { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      btn.textContent = data.synced !== undefined
        ? 'Synced ' + data.synced + ' rides'
        : (data.error || 'Error');
      setTimeout(function () {
        btn.disabled = false;
        btn.textContent = 'Sync Rides';
        if (data.synced) location.reload();
      }, 2000);
    })
    .catch(function () {
      btn.textContent = 'Error';
      setTimeout(function () { btn.disabled = false; btn.textContent = 'Sync Rides'; }, 2000);
    });
}

function getKudos(rid) {
  var spinner     = document.getElementById('kudosSpinner');
  var card        = document.getElementById('kudosCard');
  var personality = (document.getElementById('personalitySelect') || {}).value || 'default';
  if (spinner) spinner.style.display = '';
  fetch('/rides/' + rid + '/kudos', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({personality: personality})
  })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (spinner) spinner.style.display = 'none';
      if (data.kudos) {
        var el = document.getElementById('kudosText');
        if (el) {
          el.textContent = data.kudos;
        } else {
          var newEl = document.createElement('div');
          newEl.id = 'kudosText';
          newEl.style.cssText = 'line-height:1.9; white-space:pre-wrap;';
          newEl.textContent = data.kudos;
          card.insertBefore(newEl, spinner);
        }
      } else if (data.error) {
        alert('Error: ' + data.error);
      }
    })
    .catch(function () {
      if (spinner) spinner.style.display = 'none';
      alert('Request failed');
    });
}
