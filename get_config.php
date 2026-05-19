<?php
header('Content-Type: application/json');

echo json_encode([
    // OpenAI
    'OPENAI_API_KEY'          => '',
    'OPENAI_ASSISTANT_ID'     => '',
    'OPENAI_THREAD_ID'        => '',

    // Spotify
    'SPOTIFY_USERNAME'        => '',
    'SPOTIFY_CLIENT_ID'       => '',
    'SPOTIFY_CLIENT_SECRET'   => '',
    'SPOTIFY_REDIRECT_URI'    => 'http://localhost:8888/callback',

    // Overseerr / Jellyseerr
    'OVERSEERR_URL'           => 'http://localhost:5055',
    'OVERSEERR_API_KEY'       => '',
]);
