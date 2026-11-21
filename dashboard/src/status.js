export const loadStatus = (fetcher, setStatus) =>
  fetcher()
    .then((body) => setStatus(body.status))
    .catch(() => setStatus("unavailable"));
