// Local API requests run in the extension context with host_permissions.
chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (message?.type !== 'boxscreen-position') return;
    if (!sender.url || !/^https?:\/\/(www|music)\.youtube\.com\//.test(sender.url)) {
        respond({ok: false, error: 'Unsupported sender'});
        return;
    }
    fetch('http://localhost:7420/api/youtube-position', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(message.data),
        signal: AbortSignal.timeout(3000)
    }).then(response => {
        respond({ok: response.ok, error: response.ok ? null : `HTTP ${response.status}`});
    }).catch(error => respond({ok: false, error: error.message}));
    return true;
});
