import requests
import json
import time
from datetime import datetime
import os
import argparse
from http.cookiejar import MozillaCookieJar

# --- Configuration ---
GIST_URL = 'https://gist.githubusercontent.com/0x-ultra/918d4862de534339442195370b295295/raw/latest.json'
BLOCK_API_URL = 'https://x.com/i/api/1.1/blocks/create.json'

# --- Base Headers ---
# Headers that are mostly static.
# 'Cookie' and 'x-csrf-token' will be managed by the cookiejar and dynamically added for blocking.
HEADERS_FOR_GIST = {
    'accept': '*/*',
    'accept-language': 'en',
    'origin': 'https://www.stayloud.io',
    'priority': 'u=1, i',
    'referer': 'https://www.stayloud.io/',
    'sec-ch-ua': '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'cross-site',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36'
}

# Base headers for the blocking API.
# 'Cookie' and 'x-csrf-token' will be dynamically added.
# 'authorization' may still need to be updated.
BASE_HEADERS_FOR_BLOCKING = {
    'accept': '*/*',
    'accept-language': 'en',
    'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA', # KEEP THIS FRESH FROM BROWSER IF IT EXPIRES
    'content-type': 'application/x-www-form-urlencoded',
    'origin': 'https://x.com',
    'priority': 'u=1, i',
    'referer': 'https://x.com/',
    'sec-ch-ua': '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
    'x-twitter-active-user': 'yes',
    'x-twitter-auth-type': 'OAuth2Session',
    'x-twitter-client-language': 'en',
}


BLOCK_INTERVAL_SECONDS = 2
MAX_RETRIES = 3
SUCCESS_FILE = 'succeeded_blocks.txt'
FAILED_FILE = 'failed_blocks.txt'

def get_twitter_ids_from_gist(session, url, headers, proxies=None):
    """Fetches Twitter IDs and usernames from the Gist URL."""
    print("Fetching Twitter IDs and usernames from Gist...")
    try:
        response = session.get(url, headers=headers, proxies=proxies)
        response.raise_for_status()
        data = response.json()
        users_to_block = [] # Store as list of dicts: {'id': ..., 'username': ...}
        if 'leaderboard' in data and 'ok' in data['leaderboard'] and 'entries' in data['leaderboard']['ok']:
            for entry in data['leaderboard']['ok']['entries']:
                twitter_id = entry.get('twitterId')
                username = entry.get('username')
                if twitter_id:
                    users_to_block.append({'id': twitter_id, 'username': username if username else 'N/A'})
        print(f"Found {len(users_to_block)} Twitter users.")
        return users_to_block
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Gist (via proxy if set): {e}")
        return None
    except json.JSONDecodeError:
        print("Failed to decode JSON from Gist.")
        return None

def load_users_from_initial_block_file(filepath):
    """Loads Twitter IDs and usernames from a previously saved ids_to_block file."""
    print(f"Loading Twitter IDs and usernames from file: {filepath}")
    users_to_block = []
    if not os.path.exists(filepath):
        print(f"Error: Specified file '{filepath}' does not exist.")
        return None
    try:
        with open(filepath, 'r') as f:
            for line in f:
                parts = line.strip().split(',', 1)
                if len(parts) == 2:
                    users_to_block.append({'id': parts[0], 'username': parts[1]})
                elif len(parts) == 1:
                    users_to_block.append({'id': parts[0], 'username': 'N/A'})
                else:
                    print(f"Warning: Skipping malformed line in {filepath}: {line.strip()}")
        print(f"Loaded {len(users_to_block)} users from '{filepath}'.")
        return users_to_block
    except Exception as e:
        print(f"Error reading from file '{filepath}': {e}")
        return None
    
def save_users_to_file(users, filename):
    """Saves a list of user dicts (id and username) to a file."""
    with open(filename, 'w') as f:
        for user_data in users:
            f.write(f"{user_data['id']},{user_data['username']}\n")
    print(f"Saved {len(users)} users to {filename}")

def load_processed_ids_from_file(filename):
    """Loads a set of IDs from a file (assuming format 'id,username')."""
    ids = set()
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            for line in f:
                parts = line.strip().split(',', 1) # Split only on first comma to handle usernames with commas
                if parts:
                    ids.add(parts[0]) # Only store the ID in the set for quick lookup
    return ids

def append_user_to_file(user_data, filename):
    """Appends a single user (id and username) to a file."""
    with open(filename, 'a') as f:
        f.write(f"{user_data['id']},{user_data['username']}\n")

def block_twitter_id(session, user_id, base_headers, proxies=None):
    """Attempts to block a single Twitter user."""
    # Create a mutable copy of the base headers for this request
    headers = base_headers.copy()

    # Dynamically retrieve x-csrf-token from the session's cookie jar (ct0 cookie)
    csrf_token = None
    for cookie in session.cookies:
        if cookie.name == 'ct0':
            csrf_token = cookie.value
            break

    if csrf_token:
        headers['x-csrf-token'] = csrf_token
    else:
        print(f"  Warning: 'ct0' cookie (CSRF token) not found in session for {user_id}. Block might fail.")
        # Attempt to proceed without it, but it's crucial for Twitter's API
        # You might want to raise an error or return False here if it's strictly required

    data = {'user_id': user_id}
    try:
        response = session.post(BLOCK_API_URL, headers=headers, data=data, proxies=proxies)
        response.raise_for_status()  # Raise an exception for HTTP errors
        return True
    except requests.exceptions.HTTPError as e:
        print(f"  HTTP error blocking {user_id} (via proxy if set): {e.response.status_code} - {e.response.text}")
        if e.response.status_code == 403: # Forbidden, often due to invalid CSRF or auth
            print("  This might be due to an expired or invalid CSRF token or authentication.")
        return False
    except requests.exceptions.RequestException as e:
        print(f"  Network error blocking {user_id} (via proxy if set): {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Automated Twitter user blocking script.")
    parser.add_argument(
        '--proxy',
        type=str,
        help='SOCKS5H proxy address (e.g., socks5h://127.0.0.1:1084). If not set, no proxy is used.'
    )
    parser.add_argument(
        '--cookies',
        type=str,
        help='Path to a Netscape formatted file to read/write cookies from/to. E.g., cookies.txt'
    )
    parser.add_argument(
        '--file',
        type=str,
        help='Path to an existing "ids_to_block_YYYYMMDD_HHMMSS.txt" file to read block list from file instead of requesting from the web.'
    )
    args = parser.parse_args()

    # Setup proxy
    proxies = None
    if args.proxy:
        proxies = {
            'http': args.proxy,
            'https': args.proxy
        }
        print(f"Using proxy: {args.proxy}")
    else:
        print("No proxy specified. Proceeding without a proxy.")

    # Setup requests session and cookies
    session = requests.Session()
    cookie_jar = None
    if args.cookies:
        cookie_jar = MozillaCookieJar(args.cookies)
        if os.path.exists(args.cookies):
            try:
                cookie_jar.load(ignore_discard=True, ignore_expires=True)
                print(f"Loaded cookies from {args.cookies}")
            except Exception as e:
                print(f"Error loading cookies from {args.cookies}: {e}")
        else:
            print(f"Cookie file {args.cookies} not found. A new one will be created upon saving.")
        session.cookies = cookie_jar
    else:
        print("No cookie file specified. Using session cookies only (will not persist).")

    # Determine source of users to block
    users_to_block = []
    if args.file:
        all_users_to_block = load_users_from_initial_block_file(args.file)
    else:
        all_users_to_block = get_twitter_ids_from_gist(session, GIST_URL, HEADERS_FOR_GIST, proxies)

    if not all_users_to_block:
        print("No Twitter users retrieved/loaded. Exiting.")
        # Save cookies even if no users, if file was specified
        if cookie_jar:
            try:
                cookie_jar.save(ignore_discard=True, ignore_expires=True)
                print(f"Saved updated cookies to {args.cookies}")
            except Exception as e:
                print(f"Error saving cookies to {args.cookies}: {e}")
        return

    # If reading from web, save a timestamped file for future use
    if not args.file:
        current_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        initial_ids_filename = f"ids_to_block_{current_time_str}.txt"
        save_users_to_file(all_users_to_block, initial_ids_filename)
    else:
        print(f"Using block list from '{args.file}'. No new list saved.")

    # 2. Load already processed IDs (only IDs for set lookup)
    succeeded_ids_set = load_processed_ids_from_file(SUCCESS_FILE)
    print(f"Loaded {len(succeeded_ids_set)} IDs from {SUCCESS_FILE} as already succeeded.")

    # Load previously failed IDs (optional: you might want to clear this sometimes)
    failed_ids_prev_set = load_processed_ids_from_file(FAILED_FILE)
    print(f"Loaded {len(failed_ids_prev_set)} IDs from {FAILED_FILE} (from previous runs).")

    # Clear failed_blocks.txt for this run to only log current failures
    if os.path.exists(FAILED_FILE):
        os.remove(FAILED_FILE)
        print(f"Cleared {FAILED_FILE} for a fresh run's failures.")

    # 3. Process Users for blocking
    total_to_process = len(all_users_to_block)
    blocked_count = 0
    skipped_count = 0
    failed_this_run_count = 0

    print("\nStarting block process...")
    for i, user_data in enumerate(all_users_to_block):
        user_id = user_data['id']
        username = user_data['username']

        if user_id in succeeded_ids_set:
            print(f"({i+1}/{total_to_process}) Skipping {username} ({user_id}): Already succeeded.")
            skipped_count += 1
            continue
        # Uncomment the following if you want to skip previously failed IDs as well
        # if user_id in failed_ids_prev_set:
        #     print(f"({i+1}/{total_to_process}) Skipping {username} ({user_id}): Previously failed.")
        #     skipped_count += 1
        #     continue

        print(f"({i+1}/{total_to_process}) Attempting to block {username} ({user_id})...")
        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            print(f"  Attempt {attempt}/{MAX_RETRIES} for {username} ({user_id})...")
            # Pass the session and base headers, block_twitter_id will manage csrf token
            if block_twitter_id(session, user_id, BASE_HEADERS_FOR_BLOCKING, proxies):
                success = True
                break
            else:
                print(f"  Block attempt failed for {username} ({user_id}). Retrying in {BLOCK_INTERVAL_SECONDS} seconds...")
                time.sleep(BLOCK_INTERVAL_SECONDS)

        if success:
            append_user_to_file(user_data, SUCCESS_FILE)
            succeeded_ids_set.add(user_id) # Add to in-memory set to avoid re-checking file
            blocked_count += 1
            print(f"  Successfully blocked {username} ({user_id}).")
        else:
            append_user_to_file(user_data, FAILED_FILE)
            failed_this_run_count += 1
            print(f"  Failed to block {username} ({user_id}) after {MAX_RETRIES} attempts.")

        # Interval between each distinct block operation
        if i < total_to_process - 1: # Don't sleep after the last one
            print(f"Waiting {BLOCK_INTERVAL_SECONDS} seconds before next block...")
            time.sleep(BLOCK_INTERVAL_SECONDS)

    print("\n--- Process Complete ---")
    print(f"Total users found to process: {total_to_process}")
    print(f"Successfully blocked this run: {blocked_count}")
    print(f"Skipped (already in {SUCCESS_FILE}): {skipped_count}")
    print(f"Failed this run: {failed_this_run_count}")
    print(f"Remaining in {FAILED_FILE} (if any): {len(load_processed_ids_from_file(FAILED_FILE))}")

    # Save cookies back to file at the end
    if cookie_jar:
        try:
            cookie_jar.save(ignore_discard=True, ignore_expires=True)
            print(f"Saved updated cookies to {args.cookies}")
        except Exception as e:
            print(f"Error saving cookies to {args.cookies}: {e}")

if __name__ == '__main__':
    main()
