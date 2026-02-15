# Write your code here :-)
import requests
import threading
import time
import http.client
import random
#
def get_temperature(lat, lon, api_key):
    """
    Fetches the current temperature for a given city using the OpenWeatherMap API.

    Args:
        city_name (str): The name of the city.
        api_key (str): Your OpenWeatherMap API key.

    Returns:
        float: The temperature in Celsius, or None if an error occurred.
    """
    # The URL format for the current weather data API
    url = f"https://api.openweathermap.org/data/3.0/weather?lat={lat}&lon={lon}&appid={api_key}"

    try:
        # Make the GET request to the API
        response = requests.get(url)
        # Raise an exception for bad status codes (4XX or 5XX)
        response.raise_for_status()
        # Parse the JSON response
        data = response.json()

        # Extract the temperature
        temperature = data['main']['temp']
        description = data['weather'][0]['description']
        return temperature, description

    except requests.exceptions.RequestException as e:
        print(f"Error fetching weather data: {e}")
        return None, None
    except KeyError:
        print("Error: Could not parse weather data (city not found or API issue).")
        return None, None

def get_current_location():
    """
    Gets the current latitude and longitude using IP-based geolocation.
    Tries multiple services and averages results for better accuracy.

    Returns:
        tuple: A tuple containing (latitude, longitude) as floats, or (None, None) if an error occurred.
    """
    # Try multiple IP geolocation services for better accuracy
    services = [
        {
            'url': 'https://ipapi.co/json/',
            'lat_key': 'latitude',
            'lon_key': 'longitude'
        },
        {
            'url': 'https://ipinfo.io/json',
            'lat_key': 'loc',  # Returns "lat,lon" format
            'lon_key': None
        },
        {
            'url': 'http://ip-api.com/json/',
            'lat_key': 'lat',
            'lon_key': 'lon'
        }
    ]
    
    locations = []
    for service in services:
        try:
            response = requests.get(service['url'], timeout=5)
            response.raise_for_status()
            data = response.json()
            
            if service['url'] == 'https://ipinfo.io/json':
                # Special handling for ipinfo.io (returns "lat,lon" as string)
                if 'loc' in data:
                    lat, lon = map(float, data['loc'].split(','))
                    locations.append((lat, lon))
            else:
                lat = data.get(service['lat_key'])
                lon = data.get(service['lon_key'])
                if lat is not None and lon is not None:
                    locations.append((float(lat), float(lon)))
        except Exception:
            continue  # Try next service
    
    # If we got multiple results, average them for better accuracy
    if locations:
        avg_lat = sum(loc[0] for loc in locations) / len(locations)
        avg_lon = sum(loc[1] for loc in locations) / len(locations)
        return avg_lat, avg_lon
    
    # Fallback: Use ip-api.com as last resort
    try:
        response = requests.get("http://ip-api.com/json/", timeout=5)
        response.raise_for_status()
        data = response.json()
        if data.get('status') == 'success':
            return data.get('lat'), data.get('lon')
    except Exception:
        pass
    
    print("Error: Could not determine location from any service.")
    return None, None

def get_onion_headlines(rapidapi_key=None):
    """
    Fetches The Onion's latest headlines using RapidAPI or direct RSS parsing.
    
    First tries RapidAPI, then falls back to direct RSS parsing if RapidAPI fails.

    Args:
        rapidapi_key (str, optional): Your RapidAPI key for authentication.

    Returns:
        A list of headline strings, or None if an error occurs.
    """
    # Try RapidAPI first if key is provided
    if rapidapi_key:
        # Try alternative RapidAPI endpoints for RSS/News
        rapidapi_endpoints = [
            {
                'url': 'https://rss-to-json.p.rapidapi.com/feed',
                'host': 'rss-to-json.p.rapidapi.com',
                'params': {'url': 'https://www.theonion.com/rss'}
            },
            {
                'url': 'https://rss-feed-reader.p.rapidapi.com/feed',
                'host': 'rss-feed-reader.p.rapidapi.com',
                'params': {'url': 'https://www.theonion.com/rss'}
            }
        ]
        
        for endpoint in rapidapi_endpoints:
            try:
                headers = {
                    "X-RapidAPI-Key": rapidapi_key,
                    "X-RapidAPI-Host": endpoint['host']
                }
                
                response = requests.get(endpoint['url'], headers=headers, params=endpoint['params'], timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    headlines = []
                    
                    # Try various response structures
                    if 'items' in data:
                        headlines = [item.get('title', '') for item in data['items'] if item.get('title')]
                    elif 'entries' in data:
                        headlines = [entry.get('title', '') for entry in data['entries'] if entry.get('title')]
                    elif 'feed' in data and 'items' in data['feed']:
                        headlines = [item.get('title', '') for item in data['feed']['items'] if item.get('title')]
                    elif isinstance(data, list):
                        headlines = [item.get('title', '') for item in data if isinstance(item, dict) and item.get('title')]
                    
                    if headlines:
                        return headlines
                elif response.status_code == 403:
                    # If 403, try next endpoint or fall through to direct RSS parsing
                    continue
                else:
                    response.raise_for_status()
            except requests.exceptions.RequestException:
                # Try next endpoint or fall through to direct RSS parsing
                continue
    
    # Fallback: Parse RSS feed directly (no API needed)
    try:
        import feedparser
        
        rss_url = "https://www.theonion.com/rss"
        feed = feedparser.parse(rss_url)
        
        if feed.entries:
            headlines = [entry.title for entry in feed.entries if entry.title]
            return headlines
        else:
            print("No entries found in RSS feed")
            return None
            
    except ImportError:
        print("feedparser library not installed. Install it with: pip install feedparser")
        print("Falling back to basic RSS parsing...")
        
        # Basic RSS parsing without feedparser
        try:
            rss_url = "https://www.theonion.com/rss"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(rss_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Simple XML parsing for RSS
            from xml.etree import ElementTree as ET
            root = ET.fromstring(response.content)
            
            # RSS format: <item><title>...</title></item>
            headlines = []
            for item in root.findall('.//item'):
                title_elem = item.find('title')
                if title_elem is not None and title_elem.text:
                    headlines.append(title_elem.text.strip())
            
            return headlines if headlines else None
            
        except Exception as e:
            print(f"Error parsing RSS feed directly: {e}")
            return None
    except Exception as e:
        print(f"Error fetching headlines: {e}")
        return None

        
def get_temperature_text(api_key):
    # Get location once at the start
    lat, lon = get_current_location()
    if lat is None or lon is None:
        print("Error: Could not determine location. Temperature monitoring stopped.")
        return
    
    print(f"Temperature monitoring started. Location: {lat:.4f}, {lon:.4f}")
                # Query temperature
    temp_K, desc = get_temperature(lat, lon, api_key)
    
    if temp_K is not None:
        temp_C = temp_K - 273.15
        temp_F = temp_C * 9/5 + 32
        temp_F = round(temp_F, 0)
        temp_F = int(temp_F)
        
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = f"[{timestamp}] The temperature is {temp_F}°F ({temp_C:.1f}°C) with {desc}."
    else:
        text = "Failed to fetch temperature data.";
    
    return text


def get_redline_departure_text(api_key):
    """
    Fetches the next 3 inbound Red Line train arrival times at Porter Square using the MBTA V3 API.
    
    Args:
        api_key (str): Your MBTA V3 API key.
    
    Returns:
        str: A formatted string with the next 3 train arrival times, or an error message.
    """
    # Porter Square stop ID for Red Line
    stop_id = "place-portr"
    # Direction ID: 1 = inbound (toward Alewife), 0 = outbound (toward Ashmont/Braintree)
    direction_id = 1  # inbound
    route = "Red"
    
    # MBTA V3 API endpoint for predictions
    url = f"https://api-v3.mbta.com/predictions"
    
    # Parameters for filtering
    params = {
        "filter[route]": route,
        "filter[stop]": stop_id,
        "filter[direction_id]": direction_id,
        "sort": "arrival_time",
        "page[limit]": 3  # Get the next 3 trains
    }
    
    # Headers with API key
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    try:
        # Make the GET request to the API
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        
        # Parse the JSON response
        data = response.json()
        
        # Check if we have any predictions
        if not data.get('data') or len(data['data']) == 0:
            return "No inbound Red Line trains scheduled at Porter Square."
        
        # Parse the ISO 8601 timestamp
        from datetime import datetime, timezone
        
        # Get current time in UTC for comparison
        current_time_utc = datetime.now(timezone.utc)
        
        # Process up to 3 predictions
        train_times = []
        predictions = data['data'][:3]  # Get up to 3 predictions
        
        for prediction in predictions:
            attributes = prediction.get('attributes', {})
            
            # Get arrival time (prefer arrival_time, fall back to departure_time)
            arrival_time_str = attributes.get('arrival_time') or attributes.get('departure_time')
            
            if not arrival_time_str:
                continue  # Skip this prediction if no time available
            
            # Handle timezone - MBTA API returns times in UTC (Z suffix)
            if arrival_time_str.endswith('Z'):
                arrival_time_str = arrival_time_str[:-1] + '+00:00'
            
            arrival_time = datetime.fromisoformat(arrival_time_str)
            
            # Ensure arrival_time is timezone-aware (UTC)
            if arrival_time.tzinfo is None:
                arrival_time = arrival_time.replace(tzinfo=timezone.utc)
            else:
                arrival_time = arrival_time.astimezone(timezone.utc)
            
            # Calculate minutes until arrival
            time_diff = arrival_time - current_time_utc
            minutes_until = int(time_diff.total_seconds() / 60)
            
            # Format the arrival time for display (convert to local timezone)
            arrival_time_local = arrival_time.astimezone()
            time_str = arrival_time_local.strftime("%I:%M %p")
            
            # Format the time description
            if minutes_until < 0:
                time_desc = f"{time_str} (departed)"
            elif minutes_until == 0:
                time_desc = "Arriving now!"
            elif minutes_until == 1:
                time_desc = f"{time_str} (in 1 minute)"
            else:
                time_desc = f"{time_str} (in {minutes_until} minutes)"
            
            train_times.append(time_desc)
        
        if not train_times:
            return "No arrival times available for inbound Red Line trains at Porter Square."
        
        # Format the output with all train times
        result = "Next inbound Red Line trains at Porter Square:\n"
        for i, time_desc in enumerate(train_times, 1):
            result += f"  {i}. {time_desc}\n"
        
        return result.strip()  # Remove trailing newline
    
    except requests.exceptions.RequestException as e:
        return f"Error fetching MBTA data: {e}"
    except (KeyError, ValueError, IndexError) as e:
        return f"Error parsing MBTA data: {e}"
    except Exception as e:
        return f"Unexpected error: {e}"

def display_monitor(api_key, rapidapi_key, mbta_api_key, interval_minutes=10):
    """
    Continuously monitors temperature by querying every specified interval.
    This function runs in a loop and is designed to be executed in a thread.
    
    Args:
        api_key (str): OpenWeatherMap API key
        rapidapi_key (str): RapidAPI key for fetching Onion headlines
        interval_minutes (int): Interval in minutes between temperature queries (default: 10)
    """
    interval_seconds = interval_minutes * 60
    random.seed(time.time())
    while True:
        try:
            # Wait for the specified interval
            text = get_temperature_text(api_key)
            print(text)
            headlines_list = get_onion_headlines(rapidapi_key)
            number_of_headlines = len(headlines_list)
            entry = random.choice(headlines_list)
            print(entry)
            departure_text = get_redline_departure_text(mbta_api_key)
            print(departure_text)
            time.sleep(interval_seconds)
            
        except KeyboardInterrupt:
            print("\nTemperature monitoring stopped.")
            break
        except Exception as e:
            print(f"Error in temperature monitoring: {e}")
            time.sleep(interval_seconds)


def main():
    """
    Main function that spawns a thread to monitor temperature every 10 minutes.
    """
    my_api_key = "e218772439e267b3f123e89567d1909c"  # Replace with your actual OpenWeatherMap API key
    my_rapidapi_key = "e9f5733fbcmsh44e4bbf0b190180p11af65jsn134bd09fba9f"  # Replace with your actual RapidAPI key
    mbta_api_key = "3864d934cd784a05993611aa3fb428d9"

    # Create and start the temperature monitoring thread
    monitor_thread = threading.Thread(
        target=display_monitor,
        args=(my_api_key, my_rapidapi_key, mbta_api_key, 10),
        daemon=True  # Thread will stop when main program exits
    )
    
    monitor_thread.start()
    print("Temperature monitoring thread started. Press Ctrl+C to stop.\n")
    
    # Keep the main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")

if __name__ == "__main__":
    main()
