// content.js — Box Screen Lyrics Sync
// Reads YouTube video time and sends to localhost:7420

console.log('[BoxScreen] Lyrics sync loaded');

let lastPosition = -1;
let lastArtist = '';
let lastTitle = '';
let lastVideoId = '';

function getVideoId() {
    const patterns = [
        /[?&]v=([^&]+)/,
        /\/embed\/([^/?]+)/,
        /\/shorts\/([^/?]+)/,
    ];
    for (const pattern of patterns) {
        const match = window.location.href.match(pattern);
        if (match) return match[1];
    }
    return '';
}

function getVideoInfo() {
    const videoId = getVideoId();
    let title = '';
    let channel = '';

    // Try yt player response
    try {
        if (typeof window.ytplayer !== 'undefined') {
            if (window.ytplayer.getPlayerResponse && typeof window.ytplayer.getPlayerResponse === 'function') {
                const response = window.ytplayer.getPlayerResponse();
                if (response?.videoDetails) {
                    title = response.videoDetails.title || '';
                    channel = response.videoDetails.author || '';
                }
            }
            if (!title && window.ytplayer.config?.args?.title) {
                title = window.ytplayer.config.args.title;
            }
            if (!channel && window.ytplayer.config?.args?.author) {
                channel = window.ytplayer.config.args.author;
            }
        }
    } catch (e) {}

    // Try DOM
    if (!title) {
        const el = document.querySelector('h1.ytd-video-primary-info-renderer') ||
                  document.querySelector('h1.ytd-watch-metadata');
        if (el) title = el.textContent.trim();
    }
    if (!channel) {
        const el = document.querySelector('ytd-channel-name #text a') ||
                  document.querySelector('ytd-video-owner-renderer #text');
        if (el) channel = el.textContent.trim();
    }

    // Fallback
    if (!title) title = document.title?.replace(' - YouTube', '').replace(' | YouTube Music', '').trim();

    return { videoId, title, channel };
}

function getPlaybackPosition() {
    const video = document.querySelector('video.html5-main-video');
    if (video && !video.paused && !video.ended) {
        return video.currentTime;
    }
    return null;
}

function sendToBoxScreen(data) {
    fetch('http://localhost:7420/api/youtube-position', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    }).catch(err => {});
}

setInterval(() => {
    const position = getPlaybackPosition();
    if (position === null) return;

    const info = getVideoInfo();
    const videoId = info.videoId;

    if (videoId !== lastVideoId) {
        console.log('[BoxScreen] Video:', videoId, 'Channel:', info.channel, 'Title:', info.title);
    }

    const positionChanged = Math.abs(position - lastPosition) > 0.3;
    const videoChanged = videoId && videoId !== lastVideoId;

    if (!positionChanged && !videoChanged) return;

    let rawChannel = info.channel || '';
    let rawTitle = info.title || '';
    let artist = '';
    let title = rawTitle;

    // Clean title first (remove YouTube/Music suffixes)
    title = title.replace(' - YouTube', '').replace(' | YouTube Music', '').trim();

    // Check if channel looks like an official artist channel
    const isOfficialChannel = rawChannel.includes('Topic') ||
                              rawChannel.includes('VEVO') ||
                              rawChannel.includes('Music');

    // Try to extract artist from title "Artist - Song" format
    if (title.includes(' - ')) {
        const parts = title.split(' - ');
        const potentialArtist = parts[0].trim();

        // Use title's artist if:
        // 1. Channel is NOT official, OR
        // 2. Channel name matches potential artist (case-insensitive)
        if (!isOfficialChannel ||
            rawChannel.toLowerCase().includes(potentialArtist.toLowerCase())) {
            artist = potentialArtist;
            title = parts.slice(1).join(' - ').trim();
        }
    }

    // Fallback to channel if no artist found and channel is official
    if (!artist && isOfficialChannel) {
        artist = rawChannel.replace(' - Topic', '')
                          .replace('VEVO', '')
                          .replace('Topic', '')
                          .replace(/\s+/g, ' ')
                          .trim();
    }

    // Final fallback: use channel as artist
    if (!artist) {
        artist = rawChannel || 'Unknown';
    }

    // Clean up any remaining duplicates in title (e.g. "ft. X ft. X")
    const featPattern = /ft\.\s+[^\s,]+/gi;
    const feats = title.match(featPattern) || [];
    if (feats.length > 0) {
        const uniqueFeats = [...new Set(feats.map(f => f.toLowerCase()))];
        // Replace all with unique set
        title = title.replace(featPattern, '');
        if (uniqueFeats.length > 0) {
            title = title.trim() + ' ' + uniqueFeats.map(f => {
                // Capitalize properly
                const parts = f.split('.');
                return parts[0] + '. ' + parts[1].charAt(0).toUpperCase() + parts[1].slice(1);
            }).join(' ');
        }
    }

    // Remove any trailing junk
    title = title.replace(/\s*\(Official[^)]*\)/gi, '')
                 .replace(/\s*\(Audio[^)]*\)/gi, '')
                 .replace(/\s*\(Video[^)]*\)/gi, '')
                 .trim();

    sendToBoxScreen({
        videoId,
        artist: artist || 'Unknown',
        title: title || 'Unknown',
        position: Math.round(position * 10) / 10,
        timestamp: Date.now()
    });

    lastPosition = position;
    lastVideoId = videoId;
}, 500);

console.log('[BoxScreen] Watching YouTube...');
