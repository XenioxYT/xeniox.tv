import json
import time
import os
from datetime import datetime
from threading import Thread
import requests
from io import BytesIO
from colorthief import ColorThief
import colorsys
from collections import Counter

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from flask import Flask, redirect, request, url_for, session

# Spotify API credentials
CLIENT_ID = ''
CLIENT_SECRET = ''
REDIRECT_URI = 'http://localhost:5000/callback'
SCOPE = 'user-read-recently-played user-top-read user-read-playback-state'

app = Flask(__name__)
app.secret_key = ''
app.config['SESSION_COOKIE_NAME'] = ''

# Global variable to store the token info and last played track
global_token_info = None
last_played_track = None

def get_saturation(color):
    # Convert RGB to HSV to get the saturation value
    r, g, b = color[0] / 255.0, color[1] / 255.0, color[2] / 255.0
    hsv = colorsys.rgb_to_hsv(r, g, b)
    return hsv[1]  # Saturation component

def is_color_readable(color, white_threshold=230, black_threshold=50):
    # Check if the color is not too close to white or black
    return not all(c > white_threshold for c in color) and not all(c < black_threshold for c in color)

def get_most_common_saturated_color(palette):
    # Count the frequency of each color in the palette
    color_counts = Counter(palette)
    
    # Filter readable colors
    readable_colors = [color for color in color_counts.keys() if is_color_readable(color)]
    
    # Sort by saturation and frequency
    readable_colors.sort(key=lambda color: (get_saturation(color), color_counts[color]), reverse=True)
    
    # Return the most common, most saturated readable color
    return readable_colors[0] if readable_colors else (255, 255, 255)  # Fallback to white if no suitable color found

def get_dominant_color(image_url):
    try:
        response = requests.get(image_url)
        img = BytesIO(response.content)
        color_thief = ColorThief(img)
        palette = color_thief.get_palette(color_count=10)
        
        most_common_saturated_color = get_most_common_saturated_color(palette)
        
        return f"rgb({most_common_saturated_color[0]}, {most_common_saturated_color[1]}, {most_common_saturated_color[2]})"
    except Exception as e:
        print(f"Error extracting color: {e}")
        return None

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE
    )

def get_token():
    global global_token_info
    if not global_token_info:
        raise Exception("No token info")
    now = int(time.time())
    is_expired = global_token_info['expires_at'] - now < 60
    if is_expired:
        sp_oauth = create_spotify_oauth()
        global_token_info = sp_oauth.refresh_access_token(global_token_info['refresh_token'])
    return global_token_info

def get_current_track(sp):
    current_track = sp.current_playback()
    if current_track and current_track['is_playing']:
        album_art_url = current_track['item']['album']['images'][0]['url'] if current_track['item']['album']['images'] else None
        track_info = {
            'name': current_track['item']['name'],
            'artists': [artist['name'] for artist in current_track['item']['artists']],
            'album': current_track['item']['album']['name'],
            'album_art': album_art_url,
            'dominant_color': get_dominant_color(album_art_url) if album_art_url else None,
        }
        return track_info
    return None

def get_top_artist_song(sp, time_range='short_term'):
    top_tracks = sp.current_user_top_tracks(limit=1, time_range=time_range)
    top_artists = sp.current_user_top_artists(limit=1, time_range=time_range)

    top_track = None
    if top_tracks['items']:
        album_art_url = top_tracks['items'][0]['album']['images'][0]['url'] if top_tracks['items'][0]['album']['images'] else None
        top_track = {
            'name': top_tracks['items'][0]['name'],
            'artists': [artist['name'] for artist in top_tracks['items'][0]['artists']],
            'album': top_tracks['items'][0]['album']['name'],
            'album_art': album_art_url,
            'dominant_color': get_dominant_color(album_art_url) if album_art_url else None
        }

    top_artist = None
    if top_artists['items']:
        artist_image_url = top_artists['items'][0]['images'][0]['url'] if top_artists['items'][0]['images'] else None
        top_artist = {
            'name': top_artists['items'][0]['name'],
            'image': artist_image_url,
            'dominant_color': get_dominant_color(artist_image_url) if artist_image_url else None
        }

    return top_track, top_artist

def update_json():
    print("Starting update_json function")
    last_track = None
    while True:
        try:
            print("Attempting to get token")
            try:
                token_info = get_token()
                print("Token retrieved successfully")
            except Exception as e:
                print(f"Error getting token: {e}")
                time.sleep(3)
                continue

            sp = spotipy.Spotify(auth=token_info['access_token'])

            data = {
                'current_track': None,
                'recent_tracks': [],
                'top_track_week': {},
                'top_artist_week': {},
                'top_track_month': {},
                'top_artist_month': {}
            }

            try:
                with open('music_data.json', 'r') as f:
                    data = json.load(f)
                print("Existing music_data.json loaded")
            except FileNotFoundError:
                print("music_data.json not found, creating new data structure")

            print("Fetching current track")
            current_track = get_current_track(sp)
            if current_track:
                data['current_track'] = current_track
                print(f"Current track set: {current_track['name']}")

                if last_track and last_track['name'] != current_track['name']:
                    if not data['recent_tracks'] or data['recent_tracks'][0]['name'] != last_track['name']:
                        data['recent_tracks'].insert(0, last_track)
                        data['recent_tracks'] = data['recent_tracks'][:5]
                        print(f"Added to recent tracks: {last_track['name']}")

                last_track = current_track

            print("Fetching top tracks and artists")
            top_track_week, top_artist_week = get_top_artist_song(sp, 'short_term')
            data['top_track_week'] = top_track_week
            data['top_artist_week'] = top_artist_week

            top_track_month, top_artist_month = get_top_artist_song(sp, 'medium_term')
            data['top_track_month'] = top_track_month
            data['top_artist_month'] = top_artist_month

            print("Attempting to write to music_data.json")
            try:
                with open('music_data.json', 'w') as f:
                    json.dump(data, f, indent=4)
                print("Successfully wrote to music_data.json")
            except Exception as e:
                print(f"Error writing to music_data.json: {e}")
                if not os.access('.', os.W_OK):
                    print("No write permission in the current directory")

        except Exception as e:
            print(f"Error in update_json: {e}")
        
        print("Sleeping for 15 seconds")
        time.sleep(15)  # Update every 10 seconds

@app.route('/')
def index():
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
    global global_token_info
    sp_oauth = create_spotify_oauth()
    session.clear()
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)
    global_token_info = token_info
    return redirect(url_for('profile'))

@app.route('/profile')
def profile():
    global global_token_info
    if not global_token_info:
        return redirect('/')
    sp = spotipy.Spotify(auth=global_token_info['access_token'])
    current_user = sp.current_user()
    return f"<h1>Welcome, {current_user['display_name']}</h1><p>Check the console for updates.</p>"

if __name__ == '__main__':
    print("Starting the application")
    update_thread = Thread(target=update_json, daemon=True)
    update_thread.start()
    print("Update thread started")
    app.run(debug=True, use_reloader=False, host='0.0.0.0')