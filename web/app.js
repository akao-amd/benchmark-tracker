fetch("/api/status")
  .then((r) => r.json())
  .then((s) => {
    document.getElementById("status").textContent = s.name + " is up.";
  })
  .catch(() => {
    document.getElementById("status").textContent = "The server did not answer.";
  });
