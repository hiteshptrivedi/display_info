# SPDX-FileCopyrightText: 2019 ladyada for Adafruit Industries
# SPDX-License-Identifier: MIT

from os import getenv
import time

import adafruit_connection_manager
import adafruit_requests
import board
import busio
from digitalio import DigitalInOut
import displayio
from adafruit_matrixportal.matrix import Matrix
from adafruit_display_text.label import Label
from adafruit_bitmap_font import bitmap_font
#import openweather_graphics
from adafruit_matrixportal.network import Network
import gc
import microcontroller
# Use this import for adafruit_esp32spi version 11.0.0 and up.
# Note that frozen libraries may not be up to date.
# import adafruit_esp32spi
from adafruit_esp32spi import adafruit_esp32spi

# Get wifi details and more from a settings.toml file
# tokens used by this Demo: CIRCUITPY_WIFI_SSID, CIRCUITPY_WIFI_PASSWORD
ssid = getenv("CIRCUITPY_WIFI_SSID")
password = getenv("CIRCUITPY_WIFI_PASSWORD")
api_key = getenv("OPENWEATHER_API_KEY")  # Replace with your actual OpenWeatherMap API key
onionapi_key = getenv("ONION_API_KEY")  # Replace with your actual RapidAPI key
mbta_api_key = getenv("MBTA_API_KEY")
stop_id = getenv("INBOUND_MBTA_STATION")
direction_id = 1  # inbound
route = "Red"
fail_count = 0
last_time_sync_time =  time.time()
temperature_query_interval_secs = 240
redline_query_interval_secs = 60
time_sync_interval_secs = 6000

# Debug flag - set to False to disable ALL serial output (prevents hangs when USB/power connected)
DEBUG_ENABLED = False  # Set to True only when debugging with serial console

# Conditional print - no serial output when False (avoids buffer block and hang)
def debug_print(*args, **kwargs):
    if DEBUG_ENABLED:
        print(*args, **kwargs)

# Simple URL encoding function for CircuitPython
def url_encode(s):
    """Simple URL encoding - handles basic cases"""
    # For simple cases, we can just replace spaces and special chars
    # This is a minimal implementation
    s = str(s)
    # Replace spaces with %20
    s = s.replace(' ', '%20')
    # Replace other common special characters
    s = s.replace(':', '%3A')
    s = s.replace('/', '%2F')
    s = s.replace('?', '%3F')
    s = s.replace('#', '%23')
    s = s.replace('[', '%5B')
    s = s.replace(']', '%5D')
    return s

def sync_time_from_internet(requests_session, esp=None):
    """
    Fetches the current time from an internet time service and sets the system time.
    Uses worldtimeapi.org which is free and doesn't require authentication.
    
    Args:
        requests_session: The adafruit_requests session object
        esp: Optional ESP32 object for DNS resolution
    
    Returns:
        bool: True if time was successfully set, False otherwise
    """
    # Try multiple time services - using simpler/shorter hostnames that might resolve better
    time_services = [
        # Note: worldtimeapi.org now returns 410 on HTTP for many endpoints.
        # Prefer HTTP-friendly services to avoid TLS issues on CircuitPython.
        "http://timeapi.io/api/Time/current/zone?timeZone=America/New_York",
        "http://worldclockapi.com/api/json/utc/now",
    ]
    
    for service_url in time_services:
        response = None
        try:
            debug_print(f"Syncing time from: {service_url}")
            
            # Try to resolve hostname first if ESP32 object is available
            # Note: Sometimes DNS resolution fails intermittently - retry a few times
            if esp is not None:
                hostname = None
                if '://' in service_url:
                    hostname = service_url.split('://')[1].split('/')[0]
                
                # Retry DNS resolution up to 3 times
                dns_resolved = False
                for dns_retry in range(3):
                    try:
                        debug_print(f"Resolving hostname: {hostname} (attempt {dns_retry + 1}/3)")
                        ip_address = esp.get_host_by_name(hostname)
                        debug_print(f"Resolved to IP: {ip_address}")
                        dns_resolved = True
                        break
                    except Exception as dns_error:
                        if dns_retry < 2:  # Not the last attempt
                            debug_print(f"DNS resolution failed, retrying in 1 second...")
                            time.sleep(1)
                        else:
                            debug_print(f"DNS resolution failed for {hostname} after 3 attempts: {dns_error}")
                            # Continue anyway - requests_session might handle DNS internally
            
            try:
                response = requests_session.get(service_url, timeout=10)
            except Exception as ssl_error:
                error_msg = str(ssl_error)
                if "Expected 01 but got 00" in error_msg or "SSL" in error_msg:
                    debug_print(f"SSL error with time service {service_url} (trying next)")
                    continue
                else:
                    raise
            
            if response.status_code != 200:
                debug_print(f"Time service returned status code {response.status_code}")
                response.close()
                continue
            
            data = response.json()
            response.close()
            
            # Parse the datetime string from the API
            # Common fields: datetime, dateTime, currentLocalTime, currentDateTime
            datetime_str = (
                data.get("datetime")
                or data.get("dateTime")
                or data.get("currentLocalTime")
                or data.get("currentDateTime")
            )
            if not datetime_str:
                if isinstance(data, dict):
                    debug_print(f"No datetime field in response. Keys: {list(data.keys())}")
                else:
                    debug_print("No datetime field in response")
                continue
            
            # Parse the datetime string
            # Remove timezone info and microseconds for simplicity
            if 'T' in datetime_str:
                date_part, time_part = datetime_str.split('T', 1)
            else:
                debug_print("Invalid datetime format")
                continue
            
            # Remove timezone and microseconds
            time_part = time_part.split('.')[0]  # Remove microseconds
            time_part = time_part.split('+')[0]  # Remove timezone
            time_part = time_part.split('-')[0] if '-' not in time_part[:10] else time_part.rsplit('-', 1)[0]  # Remove timezone
            
            # Parse date
            year, month, day = map(int, date_part.split('-'))
            
            # Parse time
            hour, minute, second = map(int, time_part.split(':'))
            
            # Create struct_time (weekday and yearday will be calculated)
            # struct_time format: (year, month, day, hour, minute, second, weekday, yearday)
            # We'll use 0 for weekday and yearday, CircuitPython will calculate them
            time_struct = time.struct_time((year, month, day, hour, minute, second, 0, 0, 0))
            
            # Try to set the time using RTC if available
            try:
                import rtc
                r = rtc.RTC()
                # Set the datetime - RTC expects a time.struct_time
                r.datetime = time_struct
                debug_print(f"System time set to: {year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")
                # Verify it was set
                current_time = r.datetime
                debug_print(f"Verified system time: {current_time.tm_year}-{current_time.tm_mon:02d}-{current_time.tm_mday:02d} {current_time.tm_hour:02d}:{current_time.tm_min:02d}:{current_time.tm_sec:02d}")
                return True
            except ImportError:
                # RTC not available on this board
                debug_print("RTC module not available on this board")
                debug_print(f"Time from internet: {year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")
                debug_print("Note: Time cannot be set automatically without RTC module")
                return False
            except Exception as e:
                debug_print(f"Error setting RTC time: {e}")
                debug_print(f"Time from internet: {year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:{second:02d}")
                return False
                
        except Exception as e:
            error_msg = str(e)
            if "Expected 01 but got 00" in error_msg or "SSL" in error_msg:
                debug_print(f"SSL error with time service {service_url} (trying next)")
            else:
                debug_print(f"Error syncing time from {service_url}: {e}")
            try:
                response.close()
            except:
                pass
            continue
    
    debug_print("Failed to sync time from any service")
    return False


def get_temperature(lat, lon, api_key, requests_session):
    """
    Fetches the current temperature for a given city using the OpenWeatherMap API.

    Args:
        lat (float): Latitude
        lon (float): Longitude
        api_key (str): Your OpenWeatherMap API key.
        requests_session: The adafruit_requests session object

    Returns:
        float: The temperature in Celsius, or None if an error occurred.
    """
    # The URL format for the current weather data API
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=imperial"



    response = None
    try:
        # Make the GET request to the API
        # You can reuse requests_session multiple times - just call .get() or .post() again
        response = requests_session.get(url)
        # Check status code
        if response.status_code != 200:
            debug_print(f"Weather API returned status code {response.status_code}")
            response.close()
            return None, None
        
        # Parse the JSON response
        data = response.json()
        debug_print(f"weather data: {data}")
        response.close()  # Always close the response when done

        # Extract the temperature
        temperature = data['main']['temp']
        description = data['weather'][0]['description']
        return temperature, description

        # To make another query with the same session, just call it again:
        # response2 = requests_session.get(another_url)
        # ... use response2 ...
        # response2.close()  # Don't forget to close each response

    except Exception as e:
        debug_print(f"Error fetching weather data: {e}")
        try:
            response.close()
        except:
            pass
        return None, None
    except KeyError:
        debug_print("Error: Could not parse weather data (city not found or API issue).")
        return None, None

def get_forecast_summary(lat, lon, api_key, requests_session, hours_ahead=6):
    """
    Fetches a compact forecast summary using a single 5-day/3-hour forecast call.

    Returns:
        tuple: (forecast_temp, day1_label, day1_high, day2_label, day2_high) or None on error.
    """
    # Limit to ~36 hours (12 * 3-hour entries) to reduce memory usage.
    gc.collect()
    url = f"https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lon}&appid={api_key}&units=imperial&cnt=12"
    debug_print(f"forecast summary url: {url}")
    response = None
    try:
        response = requests_session.get(url)
        if response.status_code != 200:
            debug_print(f"Forecast API returned status code {response.status_code}")
            response.close()
            return None
        data = response.json()
        response.close()

        forecast_list = data.get("list", [])
        if not forecast_list:
            del data
            gc.collect()
            debug_print("No forecast data available")
            return None

        current_time = time.time()
        target_time = current_time + (hours_ahead * 3600)
        closest_forecast_temp = None
        min_time_diff = float("inf")

        day1 = time.localtime(current_time + 86400)
        day2 = time.localtime(current_time + 2 * 86400)
        day1_key = (day1.tm_year, day1.tm_mon, day1.tm_mday)
        day2_key = (day2.tm_year, day2.tm_mon, day2.tm_mday)
        highs = {day1_key: None, day2_key: None}

        for forecast in forecast_list:
            ts = forecast.get("dt")
            if ts is None:
                continue
            lt = time.localtime(ts)
            key = (lt.tm_year, lt.tm_mon, lt.tm_mday)

            temp = forecast.get("main", {}).get("temp")
            if temp is not None and key in highs:
                current_high = highs[key]
                if current_high is None or temp > current_high:
                    highs[key] = temp

            time_diff = abs(ts - target_time)
            if ts >= current_time and time_diff < min_time_diff:
                min_time_diff = time_diff
                closest_forecast_temp = temp

        del data, forecast_list
        gc.collect()

        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day1_initial = day_names[day1.tm_wday] + ":"
        day2_initial = day_names[day2.tm_wday] + ":"
        return (
            closest_forecast_temp,
            day1_initial,
            highs[day1_key],
            day2_initial,
            highs[day2_key],
        )
    except MemoryError as e:
        debug_print(f"Error fetching forecast summary: {e}")
        try:
            response.close()
        except:
            pass
        gc.collect()
        return None
    except Exception as e:
        debug_print(f"Error fetching forecast summary: {e}")
        try:
            response.close()
        except:
            pass
        return None
    except KeyError:
        debug_print("Error: Could not parse forecast summary data (API issue).")
        return None

def get_current_location(requests_session):
    """
    Gets the current latitude and longitude using IP-based geolocation.
    Tries multiple services and averages results for better accuracy.

    Args:
        requests_session: The adafruit_requests session object

    Returns:
        tuple: A tuple containing (latitude, longitude) as floats, or (None, None) if an error occurred.
    """
    # Try multiple IP geolocation services for better accuracy
    services = [
        {
            'url': 'https://ipapi.co/json/',
            'lat_key': 'latitude',
            'lon_key': 'longitude'
        }
    ]
    
    locations = []
    for service in services:
        response = None
        try:
            debug_print(f"Trying location service: {service['url']}")
            response = requests_session.get(service['url'], timeout=10)
            debug_print(f"Response status code: {response.status_code}")
            
            # Check if request was successful (status code 200)
            if response.status_code != 200:
                debug_print(f"Service {service['url']} returned status code {response.status_code}")
                response.close()
                continue
            
            data = response.json()
            debug_print(f"Response from {service['url']}: {data}")
            response.close()
            
            if service['url'] == 'https://ipinfo.io/json':
                # Special handling for ipinfo.io (returns "lat,lon" as string)
                if 'loc' in data:
                    lat, lon = map(float, data['loc'].split(','))
                    locations.append((lat, lon))
                    debug_print(f"Got location from ipinfo.io: {lat}, {lon}")
            else:
                lat = data.get(service['lat_key'])
                lon = data.get(service['lon_key'])
                if lat is not None and lon is not None:
                    locations.append((float(lat), float(lon)))
                    debug_print(f"Got location from {service['url']}: {lat}, {lon}")
                else:
                    debug_print(f"Missing lat/lon keys in response from {service['url']}")
        except Exception as e:
            debug_print(f"Error with {service['url']}: {e}")
            try:
                response.close()
            except:
                pass
            continue  # Try next service
    
    # If we got multiple results, average them for better accuracy
    if locations:
        avg_lat = sum(loc[0] for loc in locations) / len(locations)
        avg_lon = sum(loc[1] for loc in locations) / len(locations)
        return avg_lat, avg_lon
    
    # Fallback: Use ip-api.com as last resort
    response = None
    try:
        debug_print("Trying fallback service: https://ip-api.com/json/")
        response = requests_session.get("https://ip-api.com/json/", timeout=10)
        debug_print(f"Fallback response status code: {response.status_code}")
        
        if response.status_code != 200:
            debug_print(f"Fallback service returned status code {response.status_code}")
            response.close()
        else:
            data = response.json()
            debug_print(f"Fallback response: {data}")
            response.close()
            if data.get('status') == 'success':
                lat = data.get('lat')
                lon = data.get('lon')
                debug_print(f"Got location from fallback: {lat}, {lon}")
                return lat, lon
            else:
                debug_print(f"Fallback service returned status: {data.get('status')}")
    except Exception as e:
        debug_print(f"Error with fallback service: {e}")
        try:
            response.close()
        except:
            pass
    
    debug_print("Error: Could not determine location from any service.")
    return (None, None)

def get_onion_headlines(onionapi_key=None, requests_session=None):
    """
    Fetches The Onion's latest headlines using RapidAPI or direct RSS parsing.
    
    First tries RapidAPI, then falls back to direct RSS parsing if RapidAPI fails.

    Args:
        onionapi_key (str, optional): Your RapidAPI key for authentication.
        requests_session: The adafruit_requests session object

    Returns:
        A list of headline strings, or None if an error occurs.
    """
    if requests_session is None:
        debug_print("Error: requests_session is required")
        return None
    
    # Try RapidAPI first if key is provided
    if onionapi_key:
        debug_print("Using RapidAPI key: ", onionapi_key)
        # Try multiple RapidAPI endpoints for RSS/News - different APIs may have different access levels
        rapidapi_endpoints = [
            {
                'url': 'https://rss-to-json.p.rapidapi.com/feed',
                'host': 'rss-to-json.p.rapidapi.com',
                'params': {'url': 'http://www.theonion.com/rss'},  # Try HTTP first
                'method': 'GET'
            },
            {
                'url': 'https://rss-to-json.p.rapidapi.com/feed',
                'host': 'rss-to-json.p.rapidapi.com',
                'params': {'url': 'https://www.theonion.com/rss'},  # Then HTTPS
                'method': 'GET'
            },
            {
                'url': 'https://rss-feed-reader.p.rapidapi.com/feed',
                'host': 'rss-feed-reader.p.rapidapi.com',
                'params': {'url': 'http://www.theonion.com/rss'},
                'method': 'GET'
            },
            {
                'url': 'https://rss-feed-reader.p.rapidapi.com/feed',
                'host': 'rss-feed-reader.p.rapidapi.com',
                'params': {'url': 'https://www.theonion.com/rss'},
                'method': 'GET'
            },
            {
                'url': 'https://newsomaticapi.p.rapidapi.com/',
                'host': 'newsomaticapi.p.rapidapi.com',
                'params': None,  # This one uses POST with JSON body
                'method': 'POST',
                'json_payload': {
                    "api_type": "news_by_keyword_search",
                    "keyword": "The Onion"
                }
            }
        ]
        
        for endpoint in rapidapi_endpoints:
            try:
                headers = {
                    "X-RapidAPI-Key": onionapi_key,
                    "X-RapidAPI-Host": endpoint['host']
                }
                # Handle POST requests (like NewsomaticAPI)
                if endpoint.get('method') == 'POST' and endpoint.get('json_payload'):
                    headers["content-type"] = "application/json"
                    import json
                    json_data = json.dumps(endpoint['json_payload'])
                    debug_print(f"Trying POST request to: {endpoint['url']}")
                    try:
                        response = requests_session.post(endpoint['url'], data=json_data, headers=headers, timeout=10)
                    except Exception as ssl_error:
                        error_msg = str(ssl_error)
                        if "Expected 01 but got 00" in error_msg or "SSL" in error_msg:
                            debug_print(f"SSL error with RapidAPI endpoint {endpoint['url']} (skipping)")
                            continue
                        else:
                            raise
                else:
                    # Handle GET requests with query parameters
                    url = endpoint['url']
                    if endpoint.get('params'):
                        query_parts = []
                        for key, value in endpoint['params'].items():
                            # URL encode the parameters
                            query_parts.append(f"{url_encode(key)}={url_encode(str(value))}")
                        url = f"{url}?{'&'.join(query_parts)}"
                    
                    debug_print(f"Trying GET request to: {url}")
                    try:
                        response = requests_session.get(url, headers=headers, timeout=10)
                    except Exception as ssl_error:
                        # SSL/TLS errors are common in CircuitPython - skip this endpoint
                        error_msg = str(ssl_error)
                        if "Expected 01 but got 00" in error_msg or "SSL" in error_msg:
                            debug_print(f"SSL error with RapidAPI endpoint {endpoint['url']} (skipping)")
                            continue
                        else:
                            raise  # Re-raise if it's a different error
                
                debug_print(f"Response status code: {response.status_code}")
                
                if response.status_code == 200:
                    data = response.json()
                    response.close()
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
                    # Handle NewsomaticAPI response structure
                    elif 'articles' in data:
                        headlines = [article.get('title', '') for article in data['articles'] if article.get('title')]
                    elif 'data' in data and isinstance(data['data'], list):
                        headlines = [item.get('title', '') for item in data['data'] if isinstance(item, dict) and item.get('title')]
                    
                    if headlines:
                        debug_print(f"Successfully retrieved {len(headlines)} headlines from RapidAPI")
                        return headlines
                    else:
                        debug_print(f"No headlines found. Response structure: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                elif response.status_code == 403:
                    # If 403, provide more debugging info
                    debug_print(f"403 Forbidden from {endpoint['url']}")
                    debug_print("This usually means:")
                    debug_print("  - Your API key doesn't have access to this endpoint")
                    debug_print("  - The endpoint requires a paid subscription")
                    debug_print("  - Rate limiting is in effect")
                    try:
                        error_text = response.text[:200]
                        debug_print(f"Error response: {error_text}")
                    except:
                        pass
                    response.close()
                    continue
                elif response.status_code == 401:
                    debug_print(f"401 Unauthorized - Check your RapidAPI key")
                    response.close()
                    continue
                elif response.status_code != 200:
                    # If not 200, try next endpoint
                    debug_print(f"RapidAPI endpoint returned status code {response.status_code}")
                    try:
                        error_text = response.text[:200]
                        debug_print(f"Error response: {error_text}")
                    except:
                        pass
                    response.close()
                    continue
            except Exception as e:
                # Try next endpoint or fall through to direct RSS parsing
                error_msg = str(e)
                if "Expected 01 but got 00" in error_msg or "SSL" in error_msg:
                    debug_print(f"SSL error with RapidAPI endpoint {endpoint['url']} (skipping)")
                else:
                    debug_print(f"Error with RapidAPI endpoint {endpoint['url']}: {e}")
                try:
                    response.close()
                except:
                    pass
                continue
        
    # If all RSS feeds fail, return some default headlines
    debug_print("All RSS feeds failed - using default headlines")
    return [
        "News headlines unavailable due to SSL/connection limitations",
        "Temperature monitoring is working correctly",
        "Check your network connection for RSS feeds"
    ]

        
def get_temperature_text(api_key, lat, lon, requests_session):
    global fail_count
    debug_print(f"Temperature monitoring started. Location: {lat:.4f}, {lon:.4f}")
    # Query current temperature
    temp_F, desc = get_temperature(lat, lon, api_key, requests_session)
    # Query 6-hour forecast temperature + next two day highs (single API call)
    forecast_summary = get_forecast_summary(lat, lon, api_key, requests_session, hours_ahead=6)
    if forecast_summary is not None:
        forecast_temp_F, day1_initial, day1_high, day2_initial, day2_high = forecast_summary
    else:
        forecast_temp_F, day1_initial, day1_high, day2_initial, day2_high = (None, None, None, None, None)
    
    if temp_F is not None:
        temp_F = round(temp_F, 0)
        temp_F = int(temp_F)

        # Temperature is already in F 
        # Format timestamp using time module (CircuitPython compatible)
        local_time = time.localtime()
        timestamp = f"{local_time.tm_year}-{local_time.tm_mon:02d}-{local_time.tm_mday:02d} {local_time.tm_hour:02d}:{local_time.tm_min:02d}:{local_time.tm_sec:02d}"
        
        # Build text with current and forecast
        if forecast_temp_F is not None:
            forecast_temp_F = round(forecast_temp_F, 0)
            forecast_temp_F = int(forecast_temp_F)
            text = f"[{timestamp}] Now: {temp_F}°F {desc}. 6h: {forecast_temp_F}°F"
            debug_print(text)
            text = f"{temp_F}°F -> {forecast_temp_F}°F"
        else:
            text = f"[{timestamp}] The temperature is {temp_F}°F with {desc}."
            debug_print(text)
            text = f"{temp_F}°F >"
        if day1_high is not None and day2_high is not None:
            day1_high = int(round(day1_high, 0))
            day2_high = int(round(day2_high, 0))
            forecast_line = f"{day1_initial}{day1_high}\n{day2_initial}{day2_high}"
        else:
            forecast_line = "No Forecast"
        text = text + "\n" + forecast_line
        fail_count = 0
    else:
        text = "Failed to get temperature.";
        fail_count += 1
        if fail_count > 10:
            text = "Failed to fetch temperature data."
             #microcontroller.reset()
    
    return text

def get_pretty_time_text():
    # Get the current time structure
    now = time.localtime(time.time())

    # Format manually using an f-string
    # tm_hour, tm_min, and tm_sec are the attributes we need
    hour = now.tm_hour % 12
    if hour == 0: hour = 12  # Handle 12 AM/PM logic
    am_pm = "am" if now.tm_hour < 12 else "pm"

    # Get day of week abbreviation (tm_wday: 0=Monday, 6=Sunday in CircuitPython)
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    day_abbrev = day_names[now.tm_wday]

    pretty_time = f"{hour}:{now.tm_min:02}{am_pm} {day_abbrev}"

    return pretty_time

 # Parse ISO 8601 timestamps manually (CircuitPython doesn't have datetime)
def parse_iso_timestamp(iso_str):
        """Parse ISO 8601 timestamp and return (year, month, day, hour, minute, second)"""
        try:
            # Format: "2024-01-15T14:30:00-05:00" or "2024-01-15T14:30:00Z"
            # Remove 'Z' suffix if present
            if iso_str.endswith('Z'):
                iso_str = iso_str[:-1] + '+00:00'
            
            # Split date and time
            if 'T' in iso_str:
                date_part, time_part = iso_str.split('T', 1)
            else:
                date_part = iso_str.split(' ')[0]
                time_part = iso_str.split(' ')[1] if ' ' in iso_str else iso_str
            
            # Parse date
            year, month, day = map(int, date_part.split('-'))
            
            # Remove timezone offset if present
            # Look for timezone pattern: +HH:MM or -HH:MM at the end
            time_part_clean = time_part
            if '+' in time_part:
                # Positive timezone: "14:30:00+05:00"
                parts = time_part.split('+', 1)
                if len(parts) == 2 and ':' in parts[1]:
                    time_part_clean = parts[0]
            elif '-' in time_part:
                # Could be negative timezone: "14:30:00-05:00"
                # Find the last '-' and check if what follows looks like a timezone (HH:MM)
                last_dash_pos = time_part.rfind('-')
                if last_dash_pos > 0:
                    # Check if what follows the dash looks like a timezone
                    after_dash = time_part[last_dash_pos + 1:]
                    if ':' in after_dash:
                        # Split to check if it's in HH:MM format
                        tz_parts = after_dash.split(':')
                        if len(tz_parts) == 2:
                            # Check if both parts are digits (timezone format)
                            if tz_parts[0].isdigit() and tz_parts[1].isdigit():
                                # This is a timezone, remove it
                                time_part_clean = time_part[:last_dash_pos]
            
            # Remove microseconds if present (e.g., "14:30:00.123")
            if '.' in time_part_clean:
                time_part_clean = time_part_clean.split('.')[0]
            
            # Parse time (format: "HH:MM:SS")
            time_parts = time_part_clean.split(':')
            if len(time_parts) < 2:
                debug_print(f"Invalid time format: {time_part_clean} (from {time_part})")
                return None
            
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            second = int(time_parts[2]) if len(time_parts) > 2 else 0
            
            return (year, month, day, hour, minute, second)
        except Exception as e:
            debug_print(f"Error parsing timestamp from {iso_str}: {e}")
            import sys
            try:
                sys.print_exception(e)
            except AttributeError:
                pass
            return None

def get_redline_departure_text(api_key, requests_session):
    """
    Fetches the next 3 inbound Red Line train arrival times at Porter Square using the MBTA V3 API.
    Tries HTTPS first, falls back to alternative methods if SSL fails.
    
    Args:
        api_key (str): Your MBTA V3 API key.
        requests_session: The adafruit_requests session object
    
    Returns:
        str: A formatted string with the next 3 train arrival times, or an error message.
    """
    
    # Porter Square stop ID for Red Line
    # Direction ID: 1 = inbound (toward Alewife), 0 = outbound (toward Ashmont/Braintree)
    
    # Try MBTA V3 API endpoint for predictions
    url = f"https://api-v3.mbta.com/predictions"
    
    #debug_print(f"stop_id: {stop_id}")
    # Parameters for filtering 
    params = {
        "filter[route]": route,
        "filter[stop]": stop_id,
        "filter[direction_id]": direction_id,
        "sort": "arrival_time",
        "page[limit]": 10  # Fetch more predictions to filter for those >= 5 minutes away
    }
    
    # Build URL with query parameters manually (adafruit_requests doesn't support params keyword)
    query_parts = []
    for key, value in params.items():
        # URL encode the parameters
        query_parts.append(f"{url_encode(key)}={url_encode(str(value))}")
    url_with_params = f"{url}?{'&'.join(query_parts)}"
    
    # Headers with API key
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    # Avoid try/except/finally with shared variables - CircuitPython can raise
    # "local variable referenced before assignment" when using e or response in except/finally.
    try:
        resp = requests_session.get(url_with_params, headers=headers, timeout=8)
    except Exception:
        gc.collect()
        return "No MBTA data"
    try:
        if resp.status_code != 200:
            code = resp.status_code
            resp.close()
            gc.collect()
            return "MBTA API status " + str(code)
        data = resp.json()
        resp.close()
        gc.collect()
        if data is None:
            return "No MBTA data"
        if not data.get("data") or len(data["data"]) == 0:
            del data
            gc.collect()
            return "No inbound Red Line trains scheduled."

        def _minutes_until(arrival_tuple, current_time_tuple):
            if arrival_tuple is None or current_time_tuple is None:
                return None
            ya, ma, da, ha, mia, sa = arrival_tuple
            yc, mc, dc, hc, mic, sc = current_time_tuple
            arrival_min = ha * 60 + mia
            now_min = hc * 60 + mic
            if (ya, ma, da) > (yc, mc, dc):
                arrival_min += 24 * 60
            diff = arrival_min - now_min
            if diff < 0 and (ya, ma, da) == (yc, mc, dc):
                return diff
            return diff

        now = time.localtime()
        now_t = (now.tm_year, now.tm_mon, now.tm_mday, now.tm_hour, now.tm_min, now.tm_sec)
        train_times = []
        predictions_list = list(data.get("data", []))
        del data
        gc.collect()

        for prediction in predictions_list:
            attrs = prediction.get("attributes", {})
            arrival_time_str = attrs.get("arrival_time") or attrs.get("departure_time")
            if not arrival_time_str:
                continue
            at = parse_iso_timestamp(arrival_time_str)
            if at is None:
                continue
            minutes_until = _minutes_until(at, now_t)
            if minutes_until is not None and minutes_until >= 12:
                train_times.append(minutes_until)
                if len(train_times) >= 2:
                    break

        if not train_times:
            return "No arrival times available."
        result = ",".join(str(t) for t in train_times[:2]) + " mins" 
        gc.collect()
        return result
    except Exception:
        # Do not reference resp here - avoid "referenced before assignment" in CircuitPython
        gc.collect()
        return "No MBTA data"

def display_monitor(api_key, onionapi_key, mbta_api_key, requests_session, interval_seconds=10):
    """
    Continuously monitors temperature by querying every specified interval.
    This function runs in a loop and is designed to be executed in a thread.
    
    Args:
        api_key (str): OpenWeatherMap API key
        onionapi_key (str): RapidAPI key for fetching Onion headlines
        mbta_api_key (str): MBTA API key
        requests_session: The adafruit_requests session object
        interval_minutes (int): Interval in minutes between temperature queries (default: 10)
    """
    global last_time_sync_time  # Declare we're using the module-level variable
    debug_print("started display monitor")
    matrix = Matrix()
    display = matrix.display

    group = displayio.Group()  # Create a Group
    bitmap = displayio.Bitmap(64, 32, 2)  # Create a bitmap object,width, height, bit depth
    color = displayio.Palette(4)  # Create a color palette
    color[0] = 0x000000  # black background
    color[1] = 0xFF0000  # red
    color[2] = 0xCC4000  # amber
    color[3] = 0x85FF00  # greenish

    # Create a TileGrid using the Bitmap and Palette
    tile_grid = displayio.TileGrid(bitmap, pixel_shader=color)
    group.append(tile_grid)  # Add the TileGrid to the Group
    display.root_group = group

    cwd = ("/" + __file__).rsplit("/", 1)[
        0
    ]  # the current working directory (where this file is)


    #small_font = cwd + "/fonts/Arial-12.bdf"
    small_font = cwd + "/fonts/MyFont-5x7.bdf"
    #medium_font = cwd + "/fonts/Arial-14.bdf"

    small_font = bitmap_font.load_font(small_font)
    #medium_font = bitmap_font.load_font(medium_font)

    TEMP_COLOR = 0xFFA800
    MAIN_COLOR = 0x9000FF  # weather condition
    DESCRIPTION_COLOR = 0x00D3FF
    RED_COLOR = 0xFF0000
    YELLOW_COLOR = 0xFFFF00
    GREEN_COLOR = 0x00FF00
    glyphs = b"0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-,.: "
    small_font.load_glyphs(glyphs)
    #medium_font.load_glyphs(glyphs)
    #medium_font.load_glyphs(("°",))  # a non-ascii character we need for sure

    info_text_label = Label(small_font)
    info_text_label.x = 1
    info_text_label.y = 7
    info_text_label.color = DESCRIPTION_COLOR
    scrolling_text_height = 24
    scroll_delay = 0.03

    group.append(info_text_label)  # add the clock label to the group
    state = 1
    max_state = 3 

    location_found = False
    lat = None  # Initialize lat and lon to avoid "referenced before assignment" error
    last_temperature_query_time = 0
    last_redline_query_time = 0
    last_temperature_text = ""
    last_redline_text = ""

    while True:
        try:
            gc.collect()
            debug_print(f"Free memory: {gc.mem_free()}")
            if (not location_found):
                try:
                    location_result = get_current_location(requests_session)
                    # Ensure we got a valid tuple result
                    if location_result is not None and isinstance(location_result, (tuple, list)) and len(location_result) == 2:
                        try:
                            lat, lon = location_result
                            # Ensure lat and lon are valid numbers or None
                            if lat is None or lon is None:
                                lat, lon = None, None
                        except (ValueError, TypeError) as unpack_error:
                            debug_print(f"Error unpacking location result: {unpack_error}")
                            lat, lon = None, None
                    else:
                        debug_print(f"Invalid location result: {location_result}")
                        lat, lon = None, None
                except Exception as loc_error:
                    debug_print(f"Error getting location: {loc_error}")
                    lat, lon = None, None
                
                if lat is None or lon is None:
                    debug_print("Error: Could not determine location. Temperature monitoring not possible.")
                    location_found = False
                else:
                    location_found = True

            if (last_temperature_text == "" or time.time() - last_temperature_query_time > temperature_query_interval_secs):
                # Only query temperature if we have valid location
                if location_found and lat is not None and lon is not None:
                    last_temperature_text = get_temperature_text(api_key, lat, lon, requests_session)
                    last_temperature_query_time = time.time()
                    debug_print(f"updated temperature_text: {last_temperature_text}")
                else:
                    last_temperature_text = "Location not available"
                    debug_print("Skipping temperature query - location not available")
            cur_time = time.time()  # Always get current time to avoid "referenced before assignment" error
            if (last_redline_text == "" or cur_time - last_redline_query_time > redline_query_interval_secs):
                # Free memory before MBTA request to avoid allocation errors
                gc.collect()
                last_redline_text = "MBTA:\n" + get_redline_departure_text(mbta_api_key, requests_session)
                last_redline_query_time = cur_time
                gc.collect()  # Free memory after request too
                debug_print(f"updated redline_text: {last_redline_text}")
            # Initialize text and color to avoid "referenced before assignment" error
            if (cur_time - last_time_sync_time > time_sync_interval_secs):
                time_synced = sync_time_from_internet(requests_session)
                if time_synced:
                    debug_print("System time successfully synced!")
                    last_time_sync_time = cur_time
                else:
                    debug_print("Warning: Could not sync system time, using device's current time")
                    last_time_sync_time = 0
            text = ""
            color = DESCRIPTION_COLOR
            
            if state == 1:
                if (location_found):
                    color = DESCRIPTION_COLOR
                    text = last_temperature_text
                else:
                    color = RED_COLOR
                    text = "no Temp avail"
            elif state == 2:
                color = RED_COLOR
                text = last_redline_text
            elif state == 3:
                # School countdown
                try:
                    # Target: May 29, 2026
                    target_time = time.mktime((2026, 5, 29, 0, 0, 0, 0, 0, -1))
                    now = time.time()
                    diff = target_time - now
                    days_left = int(diff / 86400) + 1
                    if days_left < 0:
                        days_left = 0
                    
                    text = f"{days_left} days\nleft until"
                    
                    if days_left > 90:
                        color = RED_COLOR
                    elif days_left > 60:
                        color = YELLOW_COLOR
                    elif days_left < 30:
                        color = GREEN_COLOR
                    else:
                        color = YELLOW_COLOR

                    now_struct = time.localtime()
                    month_names = ["", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"]
                    #date_str = f"{month_names[now_struct.tm_mon]}/{now_struct.tm_mday}/{now_struct.tm_year}"
                    date_str = f"5/29/26"
                    text += f"\n{date_str}"
                except Exception as e:
                    debug_print(f"Error in state 3: {e}")
                    text = "Date Error"
                    color = RED_COLOR
            else:
                # Fallback for unknown state
                color = RED_COLOR
                text = "Unknown state"

            debug_print(f"State: {state}, Text: {text}")
            if state != 1 and state != 3:
                text += "\n" + get_pretty_time_text()
            #text = "1234567890"
            info_text_label.text = text
            info_text_label.color = color
            state += 1
            if state > max_state:
                state = 1

        except Exception:
            # Do not bind or use exception variable - avoids "referenced before assignment" in CircuitPython
            debug_print("Error in temperature monitoring")

        time.sleep(interval_seconds)  # Always sleep after each iteration

def main():
    # If you are using a board with pre-defined ESP32 Pins:
    esp32_cs = DigitalInOut(board.ESP_CS)
    esp32_ready = DigitalInOut(board.ESP_BUSY)
    esp32_reset = DigitalInOut(board.ESP_RESET)

    # If you have an AirLift Shield:
    # esp32_cs = DigitalInOut(board.D10)
    # esp32_ready = DigitalInOut(board.D7)
    # esp32_reset = DigitalInOut(board.D5)

    # If you have an AirLift Featherwing or ItsyBitsy Airlift:
    # esp32_cs = DigitalInOut(board.D13)
    # esp32_ready = DigitalInOut(board.D11)
    # esp32_reset = DigitalInOut(board.D12)

    # If you have an externally connected ESP32:
    # NOTE: You may need to change the pins to reflect your wiring
    # esp32_cs = DigitalInOut(board.D9)
    # esp32_ready = DigitalInOut(board.D10)
    # esp32_reset = DigitalInOut(board.D5)

    # Secondary (SCK1) SPI used to connect to WiFi board on Arduino Nano Connect RP2040
    if "SCK1" in dir(board):
        spi = busio.SPI(board.SCK1, board.MOSI1, board.MISO1)
    else:
        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
    esp = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)

    pool = adafruit_connection_manager.get_radio_socketpool(esp)
    ssl_context = adafruit_connection_manager.get_radio_ssl_context(esp)
    requests_session = adafruit_requests.Session(pool, ssl_context)

    if esp.status == adafruit_esp32spi.WL_IDLE_STATUS:
        debug_print("ESP32 found and in idle mode")
    debug_print("Firmware vers.", esp.firmware_version)
    debug_print("MAC addr:", ":".join("%02X" % byte for byte in esp.MAC_address))

    for ap in esp.scan_networks():
        debug_print("\t%-23s RSSI: %d" % (ap.ssid, ap.rssi))

    debug_print("Connecting to AP...")
    while not esp.is_connected:
        try:
            esp.connect_AP(ssid, password)
        except OSError as e:
            debug_print("could not connect to AP, retrying: ", e)
            continue
    debug_print("Connected to", esp.ap_info.ssid, "\tRSSI:", esp.ap_info.rssi)
    debug_print("My IP address is", esp.ipv4_address)
    
    # Test DNS resolution with a simple hostname first
    count = 0
    max_attempts = 5
    while count < max_attempts: 
        try:
            debug_print("Testing DNS resolution... attempt ", count)
            test_ip = esp.get_host_by_name("google.com")
            debug_print(f"DNS test successful: google.com -> {test_ip}")
            break
        except Exception as dns_test_error:
            debug_print(f"DNS test failed: {dns_test_error}")
            debug_print("DNS may not be ready yet")
            #time.sleep(2)
            count += 1
    
    if count >= max_attempts:
        debug_print("DNS resolution failed after max attempts. Rebooting...")
        microcontroller.reset()
    
    # Sync system time from internet
    debug_print("\nSyncing system time from internet...")
    time_synced = sync_time_from_internet(requests_session, esp)
    if time_synced:
        debug_print("System time successfully synced!")
    else:
        debug_print("Warning: Could not sync system time, using device's current time")
    last_time_sync_time = time.time()
    debug_print(f"1 last_time_sync_time: {last_time_sync_time}")
    debug_print(f"Current system time: {time.localtime()}")
    debug_print()
    
    debug_print("Done! starting display monitor...")
    

    display_monitor(api_key, onionapi_key, mbta_api_key, requests_session, interval_seconds=10)


if __name__ == "__main__":
    main()