import requests
import re
import json
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import os
from telegram import Bot
from telegram.ext import Application, CommandHandler
import asyncio
import urllib.parse

# Common headers
BASE_HEADERS = {
    "Host": "www.ivasms.com",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-GB,en;q=0.9",
    "Priority": "u=0, i",
    "Connection": "keep-alive"
}

async def send_to_telegram(sms):
    """Send SMS details to Telegram group with copiable number."""
    bot = Bot(token=os.getenv("BOT_TOKEN"))
    message = (
        f"New SMS Received:\n"
        f"Timestamp: {sms['timestamp']}\n"
        f"Number: +{sms['number']}\n"
        f"Message: {sms['message']}\n"
        f"Range: {sms['range']}\n"
        f"Revenue: {sms['revenue']}"
    )
    try:
        await bot.send_message(chat_id=os.getenv("CHAT_ID"), text=message)
        print(f"Sent SMS to Telegram: {sms['message'][:50]}...")
    except Exception as e:
        print(f"Failed to send to Telegram: {str(e)}")

def payload_1(session):
    """Send GET request to /login to retrieve initial tokens."""
    url = "https://www.ivasms.com/login"
    headers = BASE_HEADERS.copy()
    response = session.get(url, headers=headers)
    response.raise_for_status()
    
    token_match = re.search(r'<input type="hidden" name="_token" value="([^"]+)"', response.text)
    if not token_match:
        raise ValueError("Could not find _token in response")
    return {"_token": token_match.group(1)}

def payload_2(session, _token):
    """Send POST request to /login with credentials."""
    url = "https://www.ivasms.com/login"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded",
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://www.ivasms.com/login"
    })
    
    data = {
        "_token": _token,
        "email": os.getenv("IVASMS_EMAIL"),
        "password": os.getenv("IVASMS_PASSWORD"),
        "remember": "on",
        "g-recaptcha-response": "",
        "submit": "register"
    }
    
    response = session.post(url, headers=headers, data=data)
    response.raise_for_status()
    if response.url.endswith("/login"):
        raise ValueError("Login failed, redirected back to /login")
    return response

def payload_3(session):
    """Send GET request to /sms/received to get statistics page."""
    url = "https://www.ivasms.com/portal/sms/received"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://www.ivasms.com/portal"
    })
    
    response = session.get(url, headers=headers)
    response.raise_for_status()
    
    # Extract CSRF token from response
    token_match = re.search(r'<meta name="csrf-token" content="([^"]+)">', response.text)
    if not token_match:
        return ""
    return {"csrf_token": token_match.group(1)}

def payload_4(session, csrf_token, from_date_str, to_date_str):
    """Send POST request to /sms/received/getsms to fetch SMS statistics."""
    url = "https://www.ivasms.com/portal/sms/received/getsms"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundary",
        "charset": "UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "data": "empty",
        "Referer": "same-origin",
        "Origin": "https://www.ivasms.com"
    })
    
    data = {
        "_token": csrf_token,
        "from": from_date_str,
        "to": to_date_str
    }
    
    response = session.post(url, headers=headers, data=data)
    response.raise_for_status()
    return response

def parse_statistics(response_text):
    """Parse SMS statistics from response."""
    soup = BeautifulSoup(response_text, 'html.parser')
    ranges = []
    
    # Check for "no SMS" message
    no_sms = soup.find('p', id='messageFlash')
    if no_sms and "SMS" in no_sms.text.lower():
        return ranges
    
    # Parse range cards
    range_cards = soup.find_all('div', class_='card card-body mb-1 pointer')
    for card in range_cards:
        cols = card.find_all('div', class_=re.compile(r'col-sm-\d+|col-\d+'))
        if len(cols) >= 5:
            range_name = cols[0].text.strip()
            count_text = cols[1].find('p').text.strip()
            paid_text = cols[2].find('p').text.strip()
            unpaid_text = cols[3].find('p').text.strip()
            revenue_span = cols[4].find('span', class_='currency_cdr')
            revenue_text = revenue_span.text.strip() if revenue_span else "0.0"
            
            try:
                count = int(count_text) if count_text else 0
                paid = int(paid_text) if paid_text else 0
                unpaid = int(unpaid_text) if unpaid_text else 0
                revenue = float(revenue_text) if revenue_text else 0.0
            except ValueError:
                count, paid, unpaid, revenue = 0, 0, 0, 0.0
            
            onclick = card.get('onclick', '')
            range_id_match = re.search(r"getDetials\('([^']+)'\)", onclick)
            range_id = range_id_match.group(1) if range_id_match else range_name
            
            ranges.append({
                "range_name": range_name,
                "range_id": range_id,
                "count": count,
                "paid": paid,
                "unpaid": unpaid,
                "revenue": revenue
            })
    
    return ranges

def save_to_json(data, filename="sms_statistics.json"):
    """Save range data to JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        print("Range saved in memory")
    except Exception as e:
        print(f"Failed to save to JSON: {str(e)}")

def load_from_json(filename="sms_statistics.json"):
    """Load range data from JSON file."""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []
    except Exception as e:
        print(f"Failed to load from JSON: {str(e)}")
        return []

def payload_5(session, csrf_token, to_date, range_name):
    """Send POST request to /sms/received/getsms/number to get numbers for a range."""
    url = "https://www.ivasms.com/portal/sms/received/getsms/number"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "data": "empty",
        "Referer": "same-origin",
        "Origin": "https://www.ivasms.com"
    })
    
    data = {
        "_token": csrf_token,
        "start": "",
        "end": to_date,
        "range": range_name
    }
    
    response = session.post(url, headers=headers, data=data)
    response.raise_for_status()
    return response

def parse_numbers(response_text):
    """Parse numbers from the range response."""
    soup = BeautifulSoup(response_text, 'html.parser')
    numbers = []
    
    number_divs = soup.find_all('div', class_='card card-body border-bottom bg-100 p-2 rounded-0')
    for div in number_divs:
        onclick = div.find('div', class_=re.compile(r'col-sm-\d+|col-\d+')).get('onclick', '')
        match = re.search(r"'([^']+)','([^']+)'", onclick)
        if match:
            number, number_id = match.groups()
            numbers.append({"number": number, "number_id": number_id})
    
    return numbers

def payload_6(session, csrf_token, to_date, number, range_name):
    """Send POST request to /sms/received/getsms/number/sms to get message details."""
    url = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "data": "empty",
        "Referer": "same-origin",
        "Origin": "https://www.ivasms.com"
    })
    
    data = {
        "_token": csrf_token,
        "start": "",
        "end": to_date,
        "Number": number,
        "Range": range_name
    }
    
    response = session.post(url, headers=headers, data=data)
    response.raise_for_status()
    return response

def parse_message(response_text):
    """Parse message details from response."""
    soup = BeautifulSoup(response_text, 'html.parser')
    message_div = soup.find('div', class_='col-9 col-sm-6 text-center text-sm-start')
    revenue_div = soup.find('div', class_='col-3 col-sm-2 text-center text-sm-start')
    
    message = message_div.find('p').text.strip() if message_div else "No message found"
    revenue = revenue_div.find('span', class_='currency_cdr').text.strip() if revenue_div else "0.0"
    return {"message": message, "revenue": revenue}

async def start_command(update, context):
    """Handle /start command in Telegram."""
    await update.message.reply_text("IVASMS Bot started! Monitoring SMS statistics.")

async def main():
    """Main function to execute automation and monitor SMS statistics."""
    # Set up Telegram bot with polling
    application = Application.builder().token(os.getenv("BOT_TOKEN")).build()
    application.add_handler(CommandHandler("start", start_command))
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Calculate date range
    today = datetime.now()
    from_date = today.strftime("%m/%d/%Y")
    to_date = (today + timedelta(days=1)).strftime("%m/%d/%Y")
    
    # Initialize in-memory storage
    JSON_FILE = "sms_statistics.json"
    session_start = time.time()
    existing_ranges = load_from_json(JSON_FILE)
    existing_ranges_dict = {r["range_name"]: r for r in existing_ranges}
    
    while True:
        try:
            with requests.Session() as session:
                # Step 1: Login
                tokens = payload_1(session)
                payload_2(session, tokens["_token"])
                response, csrf_token = payload_3(session)
                
                # Step 2: Fetch initial statistics
                response = payload_4(session, csrf_token, from_date, to_date)
                ranges = parse_statistics(response.text)
                
                # Save initial statistics if empty
                if not existing_ranges:
                    existing_ranges = ranges
                    existing_ranges_dict = {r["range_name"]: r for r in ranges}
                    save_to_json(existing_ranges, JSON_FILE)
                
                # Step 3: Continuous monitoring
                while True:
                    # Check for session expiry (2 hours)
                    if time.time() - session_start > 7200:
                        break
                    
                    # Fetch updated statistics
                    response = payload_4(session, csrf_token, from_date, to_date)
                    new_ranges = parse_statistics(response.text)
                    new_ranges_dict = {r["range_name"]: r for r in new_ranges}
                    
                    # Compare with existing ranges
                    for range_data in new_ranges:
                        range_name = range_data["range_name"]
                        current_count = range_data["count"]
                        existing_range = existing_ranges_dict.get(range_name)
                        
                        if not existing_range:
                            print(f"New range detected: {range_name}")
                            response = payload_5(session, csrf_token, to_date, range_name)
                            numbers = parse_numbers(response.text)
                            if numbers:
                                for number_data in numbers[::-1]:
                                    print(f"Fetching message for number: {number_data['number']}")
                                    response = payload_6(session, csrf_token, to_date, number_data["number"], range_name)
                                    message_data = parse_message(response.text)
                                    
                                    sms = {
                                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "number": number_data["number"],
                                        "message": message_data["message"],
                                        "range": range_name,
                                        "revenue": message_data["revenue"]
                                    }
                                    print(f"New SMS: {sms}")
                                    await send_to_telegram(sms)
                                
                                existing_ranges.append(range_data)
                                existing_ranges_dict[range_name] = range_data
                        
                        elif current_count > existing_range["count"]:
                            count_diff = current_count - existing_range["count"]
                            print(f"Count increased for {range_name}: {existing_range['count']} -> {current_count} (+{count_diff})")
                            response = payload_5(session, csrf_token, to_date, range_name)
                            numbers = parse_numbers(response.text)
                            if numbers:
                                for number_data in numbers[-count_diff:][::-1]:
                                    print(f"Fetching message for number: {number_data['number']}")
                                    response = payload_6(session, csrf_token, to_date, number_data["number"], range_name)
                                    message_data = parse_message(response.text)
                                    
                                    sms = {
                                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        "number": number_data["number"],
                                        "message": message_data["message"],
                                        "range": range_name,
                                        "revenue": message_data["revenue"]
                                    }
                                    print(f"New SMS: {sms}")
                                    await send_to_telegram(sms)
                                
                                for r in existing_ranges:
                                    if r["range_name"] == range_name:
                                        r["count"] = current_count
                                        r["paid"] = range_data["paid"]
                                        r["unpaid"] = range_data["unpaid"]
                                        r["revenue"] = range_data["revenue"]
                                        break
                                existing_ranges_dict[range_name] = range_data
                    
                    # Update existing ranges
                    existing_ranges = new_ranges
                    existing_ranges_dict = new_ranges_dict
                    save_to_json(existing_ranges, JSON_FILE)
                    
                    # Wait 2-3 seconds
                    await asyncio.sleep(2 + (time.time() % 1))
                
        except Exception as e:
            print(f"Error: {str(e)}. Retrying in 30 seconds...")
            await asyncio.sleep(30)

if __name__ == "__main__":
    asyncio.run(main())