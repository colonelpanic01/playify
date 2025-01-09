import os
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, session, request
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from statistics import mean

app = Flask(__name__)
app.secret_key = os.urandom(24)
load_dotenv()

# Spotify API credentials
SPOTIPY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIPY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI')
SCOPE = 'user-library-read'

# Mood classification thresholds
MOOD_THRESHOLDS = {
    'Happy': {'valence': 0.7, 'energy': 0.7},
    'Sad': {'valence': 0.3, 'energy': 0.4},
    'Energetic': {'valence': 0.5, 'energy': 0.8},
    'Chill': {'valence': 0.5, 'energy': 0.3},
    'Balanced': {'valence': 0.5, 'energy': 0.5}
}

def get_spotify():
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE,
        cache_path='.cache'
    ))

def get_season(date):
    month = date.month
    if month in [12, 1, 2]:
        return 'Winter'
    elif month in [3, 4, 5]:
        return 'Spring'
    elif month in [6, 7, 8]:
        return 'Summer'
    else:
        return 'Fall'

def classify_mood(audio_features):
    if not audio_features:
        return 'Unknown'
    
    valence = audio_features['valence']
    energy = audio_features['energy']
    
    if valence >= MOOD_THRESHOLDS['Happy']['valence'] and energy >= MOOD_THRESHOLDS['Happy']['energy']:
        return 'Happy'
    elif valence <= MOOD_THRESHOLDS['Sad']['valence'] and energy <= MOOD_THRESHOLDS['Sad']['energy']:
        return 'Sad'
    elif energy >= MOOD_THRESHOLDS['Energetic']['energy']:
        return 'Energetic'
    elif energy <= MOOD_THRESHOLDS['Chill']['energy']:
        return 'Chill'
    else:
        return 'Balanced'

def get_track_genres(sp, track):
    # Get genres from artist
    artist_id = track['artists'][0]['id']
    artist_info = sp.artist(artist_id)
    return artist_info['genres']

def fetch_liked_songs(sp, start_date, end_date, group_by, genre_filter=None, mood_filter=None):
    songs_by_period = {}
    offset = 0
    batch_size = 50
    continue_fetching = True
    
    while continue_fetching:
        results = sp.current_user_saved_tracks(limit=batch_size, offset=offset)
        
        if not results['items']:
            break
        
        # Get audio features for the batch of tracks
        track_ids = [item['track']['id'] for item in results['items']]
        audio_features = sp.audio_features(track_ids)

        for item, features in zip(results['items'], audio_features):
            track = item['track']
            added_at = datetime.strptime(item['added_at'], '%Y-%m-%dT%H:%M:%SZ')
            
            if added_at < start_date:
                continue_fetching = False
                break
                
            if start_date <= added_at <= end_date:
                # Get genres and classify mood
                genres = get_track_genres(sp, track)
                mood = classify_mood(features)
                
                # Apply filters if specified
                if genre_filter and not any(genre_filter.lower() in genre.lower() for genre in genres):
                    continue
                if mood_filter and mood != mood_filter:
                    continue
                
                # Determine the grouping key based on user selection
                if group_by == 'monthly':
                    period_key = added_at.strftime('%B %Y')
                elif group_by == 'seasonal':
                    period_key = f"{get_season(added_at)} {added_at.year}"
                else:  # yearly
                    period_key = str(added_at.year)
                
                if period_key not in songs_by_period:
                    songs_by_period[period_key] = []
                
                songs_by_period[period_key].append({
                    'name': track['name'],
                    'artists': ', '.join(artist['name'] for artist in track['artists']),
                    'id': track['id'],
                    'added_at': added_at.strftime('%Y-%m-%d'),
                    'preview_url': track.get('preview_url'),
                    'external_url': track['external_urls']['spotify'],
                    'genres': genres,
                    'mood': mood,
                    'audio_features': {
                        'valence': features['valence'],
                        'energy': features['energy'],
                        'danceability': features['danceability'],
                        'tempo': features['tempo']
                    }
                })
        
        offset += batch_size
        if len(results['items']) < batch_size:
            break

    return songs_by_period

@app.route('/')
def index():
    if not session.get('token_info'):
        return render_template('login.html')
    return redirect(url_for('dashboard'))

@app.route('/login')
def login():
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE
    )
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
    sp_oauth = SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE
    )
    session.clear()
    code = request.args.get('code')
    token_info = sp_oauth.get_access_token(code)
    session['token_info'] = token_info
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    if not session.get('token_info'):
        return redirect(url_for('login'))

    sp = get_spotify()
    # Get a sample of genres for the dropdown
    # We'll get genres from the user's recent tracks
    results = sp.current_user_saved_tracks(limit=50)
    all_genres = set()
    for item in results['items']:
        genres = get_track_genres(sp, item['track'])
        all_genres.update(genres)
    
    return render_template('dashboard.html', 
                         genres=sorted(all_genres),
                         moods=list(MOOD_THRESHOLDS.keys()))


@app.route('/fetch-songs', methods=['POST'])
def fetch_songs():
    if not session.get('token_info'):
        return redirect(url_for('login'))
    
    start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d')
    end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d')
    group_by = request.form['group_by']
    genre_filter = request.form.get('genre')
    mood_filter = request.form.get('mood')
    
    sp = get_spotify()
    songs_by_period = fetch_liked_songs(sp, start_date, end_date, group_by, 
                                      genre_filter, mood_filter)
    
    return render_template('results.html', 
                         songs_by_period=songs_by_period, 
                         group_by=group_by)

if __name__ == '__main__':
    app.run(debug=True)
