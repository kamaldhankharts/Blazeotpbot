import requests
import re
import json
import time
import logging
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import os
from telegram import Bot
from telegram.ext import Application, CommandHandler
import asyncio
import urllib.parse

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
        "📨 *New SMS Received*\n\n"
        f"📞 *Number*: `+{sms['number']}`\n"
        f"🌐 *Range*: `{sms['range']}`\n"
        f"💬 *Message*: {sms['message']}\n"
        f"🕒 *Time*: {sms['timestamp']}\n"
    )

    try:
        await bot.send_message(chat_id=os.getenv("CHAT_ID"), text=message, parse_mode="Markdown")
        logger.info(f"Sent SMS to Telegram: {sms['message'][:50]}...")
    except Exception as e:
        logger.error(f"Failed to send to Telegram: {str(e)}")

def payload_1(session):
    """Send GET request to /login to retrieve initial tokens."""
    url = "https://www.ivasms.com/login"
    headers = BASE_HEADERS.copy()
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        token_match = re.search(r'<input type="hidden" name="_token" value="([^"]+)"', response.text)
        if not token_match:
            raise ValueError("Could not find _token in response")
        return {"_token": token_match.group(1)}
    except Exception as e:
        logger.error(f"Payload 1 failed: {str(e)}")
        raise

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
        "submit": "Login"
    }
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        if response.url.endswith("/login"):
            raise ValueError("Login failed, redirected back to /login")
        return response
    except Exception as e:
        logger.error(f"Payload 2 failed: {str(e)}")
        raise

def payload_3(session):
    """Send GET request to /sms/received to get statistics page."""
    url = "https://www.ivasms.com/portal/sms/received"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Sec-Fetch-Site": "same-origin",
        "Referer": "https://www.ivasms.com/portal"
    })
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        token_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.text)
        if not token_match:
            logger.warning("No CSRF token found in /sms/received response")
            return ""
        return response, token_match.group(1)
    except Exception as e:
        logger.error(f"Payload 3 failed: {str(e)}")
        raise

def payload_4(session, csrf_token, from_date, to_date):
    """Send POST request to /sms/received/getsms to fetch SMS statistics."""
    url = "https://www.ivasms.com/portal/sms/received/getsms"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "multipart/form-data; boundary=----WebKitFormBoundaryhkp0qMozYkZV6Ham",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/sms/received",
        "Origin": "https://www.ivasms.com"
    })
    
    data = (
        "------WebKitFormBoundaryhkp0qMozYkZV6Ham\r\n"
        "Content-Disposition: form-data; name=\"from\"\r\n"
        "\r\n"
        f"{from_date}\r\n"
        f"------WebKitFormBoundaryhkp0qMozYkZV6Ham\r\n"
        "Content-Disposition: form-data; name=\"to\"\r\n"
        "\r\n"
        f"{to_date}\r\n"
        f"------WebKitFormBoundaryhkp0qMozYkZV6Ham\r\n"
        "Content-Disposition: form-data; name=\"_token\"\r\n"
        "\r\n"
        f"{csrf_token}\r\n"
        "------WebKitFormBoundaryhkp0qMozYkZV6Ham--\r\n"
    )
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Payload 4 failed: {str(e)}")
        raise

def payload_5(session, csrf_token, to_date, range_name):
    """Send POST request to /sms/received/getsms/number to get numbers for a range."""
    url = "https://www.ivasms.com/portal/sms/received/getsms/number"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/sms/received",
        "Origin": "https://www.ivasms.com"
    })
    
    data = {
        "_token": csrf_token,
        "start": "",
        "end": to_date,
        "range": range_name
    }
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Payload 5 failed: {str(e)}")
        raise

def payload_6(session, csrf_token, to_date, number, range_name):
    """Send POST request to /sms/received/getsms/number/sms to get message details."""
    url = "https://www.ivasms.com/portal/sms/received/getsms/number/sms"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/sms/received",
        "Origin": "https://www.ivasms.com"
    })
    
    data = {
        "_token": csrf_token,
        "start": "",
        "end": to_date,
        "Number": number,
        "Range": range_name
    }
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Payload 6 failed: {str(e)}")
        raise

def payload_7(session, app="WhatsApp"):
    """Send GET request to /portal/sms/test/sms to get available ranges."""
    url = f"https://www.ivasms.com/portal/sms/test/sms?app={urllib.parse.quote(app)}&draw=1&columns%5B0%5D%5Bdata%5D=range&columns%5B0%5D%5Borderable%5D=false&columns%5B1%5D%5Bdata%5D=termination.test_number&columns%5B1%5D%5Bsearchable%5D=false&columns%5B1%5D%5Borderable%5D=false&columns%5B2%5D%5Bdata%5D=originator&columns%5B2%5D%5Borderable%5D=false&columns%5B3%5D%5Bdata%5D=messagedata&columns%5B3%5D%5Borderable%5D=false&columns%5B4%5D%5Bdata%5D=senttime&columns%5B4%5D%5Bsearchable%5D=false&order%5B0%5D%5Bcolumn%5D=4&order%5B0%5D%5Bdir%5D=desc&start=0&length=25&search%5Bvalue%5D=&_={int(time.time() * 1000)}"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://www.ivasms.com/portal/sms/test/sms?app={urllib.parse.quote(app)}"
    })
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload 7 failed: {str(e)}")
        raise

def payload_8(session, csrf_token, number_ids):
    """Send POST request to /portal/numbers/return/number/bluck to delete specific numbers."""
    url = "https://www.ivasms.com/portal/numbers/return/number/bluck"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers",
        "Origin": "https://www.ivasms.com"
    })
    
    data = {"NumberID[]": number_ids}
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload 8 failed: {str(e)}")
        raise

def payload_9(session, csrf_token):
    """Send POST request to /portal/numbers/return/allnumber/bluck to delete all numbers."""
    url = "https://www.ivasms.com/portal/numbers/return/allnumber/bluck"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers",
        "Origin": "https://www.ivasms.com"
    })
    
    try:
        response = session.post(url, headers=headers, data={}, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload 9 failed: {str(e)}")
        raise

def parse_statistics(response_text):
    """Parse SMS statistics from response and return range data."""
    try:
        soup = BeautifulSoup(response_text, 'html.parser')
        ranges = []
        
        no_sms = soup.find('p', id='messageFlash')
        if no_sms and "You do not have any SMS" in no_sms.text:
            logger.info("No SMS data found in response")
            return ranges
        
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
                except ValueError as e:
                    logger.warning(f"Error parsing values for {range_name}: {str(e)}")
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
    except Exception as e:
        logger.error(f"Parse statistics failed: {str(e)}")
        raise

def parse_numbers(response_text):
    """Parse numbers from the range response."""
    try:
        soup = BeautifulSoup(response_text, 'html.parser')
        numbers = []
        
        number_divs = soup.find_all('div', class_='card card-body border-bottom bg-100 p-2 rounded-0')
        for div in number_divs:
            onclick = div.find('div', class_=re.compile(r'col-sm-\d+|col-\d+')).get('onclick', '')
            match = re.search(r"'([^']+)','([^']+)'", onclick)
            if match:
                number, number_id = match.groups()
                numbers.append({"number": number, "number_id": number_id})
            else:
                logger.warning(f"Failed to parse onclick: {onclick}")
        return numbers
    except Exception as e:
        logger.error(f"Parse numbers failed: {str(e)}")
        raise

def parse_message(response_text):
    """Parse message details from response."""
    try:
        soup = BeautifulSoup(response_text, 'html.parser')
        message_rows = soup.find_all('tr')
        messages = []
        
        for row in message_rows:
            message_div = row.find('div', class_='col-9 col-sm-6 text-center text-sm-start')
            revenue_div = row.find('div', class_='col-3 col-sm-2 text-center text-sm-start')
            timestamp_div = row.find('div', class_='col-12 col-sm-4 text-center text-sm-start')
            
            message = message_div.find('p').text.strip() if message_div else "No message found"
            revenue = revenue_div.find('span', class_='currency_cdr').text.strip() if revenue_div else "0.0"
            timestamp = timestamp_div.find('p').text.strip() if timestamp_div else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            messages.append({
                "message": message,
                "revenue": revenue,
                "timestamp": timestamp
            })
        
        return messages
    except Exception as e:
        logger.error(f"Parse message failed: {str(e)}")
        raise

def parse_ranges(response_json):
    """Parse available ranges from JSON response."""
    try:
        ranges = set()
        for item in response_json.get('data', []):
            range_name = item.get('range', '')
            if range_name:
                ranges.add(range_name)
        return sorted(list(ranges))
    except Exception as e:
        logger.error(f"Parse ranges failed: {str(e)}")
        return []

def save_to_json(data, filename):
    """Save data to JSON file."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        logger.info(f"Data saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to save to JSON {filename}: {str(e)}")

def load_from_json(filename):
    """Load data from JSON file."""
    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except Exception as e:
        logger.error(f"Failed to load from JSON {filename}: {str(e)}")
        return {}

async def start_command(update, context):
    """Handle /start command in Telegram."""
    try:
        await update.message.reply_text("IVASMS Bot started! Monitoring SMS statistics.")
        logger.info("Processed /start command")
    except Exception as e:
        logger.error(f"Start command failed: {str(e)}")

async def check_command(update, context):
    """Handle /check command to list available ranges."""
    try:
        with requests.Session() as session:
            # Login
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            
            # Fetch ranges
            response = payload_7(session)
            ranges = parse_ranges(response)
            
            if not ranges:
                await update.message.reply_text("No ranges found.", parse_mode="Markdown")
                return
            
            message = "📋 *Available Ranges*:\n\n" + "\n".join([f"`{range_name}`" for range_name in ranges])
            await update.message.reply_text(message, parse_mode="Markdown")
            logger.info("Processed /check command")
    except Exception as e:
        logger.error(f"Check command failed: {str(e)}")
        await update.message.reply_text(f"Error fetching ranges: {str(e)}", parse_mode="Markdown")

async def main():
    """Main function to execute automation and monitor SMS statistics."""
    try:
        # Set up Telegram bot with polling
        application = Application.builder().token(os.getenv("BOT_TOKEN")).build()
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("check", check_command))
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Telegram bot started")
        
        # Calculate date range
        today = datetime.now()
        from_date = today.strftime("%m/%d/%Y")
        to_date = (today + timedelta(days=1)).strftime("%m/%d/%Y")
        
        # Initialize storage
        JSON_FILE = "sms_statistics.json"
        NUMBER_TRACKER_FILE = "number_tracker.json"
        existing_ranges = load_from_json(JSON_FILE)
        existing_ranges_dict = {r["range_name"]: r for r in existing_ranges}
        number_tracker = load_from_json(NUMBER_TRACKER_FILE)
        
        # Initialize number tracker if empty
        if not number_tracker:
            number_tracker = {}
        
        last_reauth_time = 0
        min_reauth_interval = 60
        
        while True:
            try:
                with requests.Session() as session:
                    session_start = time.time()
                    
                    # Login
                    logger.info("Executing Payload 1: GET /login")
                    tokens = payload_1(session)
                    
                    logger.info("Executing Payload 2: POST /login")
                    response = payload_2(session, tokens["_token"])
                    logger.debug(f"Payload 2 response status: {response.status_code}, URL: {response.url}")
                    
                    logger.info("Executing Payload 3: GET /sms/received")
                    response, csrf_token = payload_3(session)
                    logger.debug(f"Payload 3 response status: {response.status_code}")
                    
                    # Fetch initial statistics
                    logger.info(f"Executing Payload 4: POST /sms/received/getsms for date range {from_date} to {to_date}")
                    response = payload_4(session, csrf_token, from_date, to_date)
                    logger.debug(f"Payload 4 response status: {response.status_code}")
                    ranges = parse_statistics(response.text)
                    
                    if not existing_ranges:
                        existing_ranges = ranges
                        existing_ranges_dict = {r["range_name"]: r for r in ranges}
                        save_to_json(existing_ranges, JSON_FILE)
                    
                    while True:
                        # Session validation
                        try:
                            test_response = session.get("https://www.ivasms.com/portal", headers=BASE_HEADERS, timeout=10)
                            if test_response.status_code == 401 or test_response.url.endswith("/login"):
                                logger.info("Session invalid. Re-authenticating...")
                                last_reauth_time = time.time()
                                break
                        except Exception as e:
                            logger.warning(f"Session validation check failed: {str(e)}")
                            last_reauth_time = time.time()
                            break
                        
                        # Check session expiry
                        elapsed_time = time.time() - session_start
                        logger.debug(f"Session elapsed time: {elapsed_time:.2f} seconds")
                        if elapsed_time > 7200:
                            logger.info("Session nearing expiry. Re-authenticating...")
                            time_since_last_reauth = time.time() - last_reauth_time
                            if time_since_last_reauth < min_reauth_interval:
                                logger.info(f"Waiting {min_reauth_interval - time_since_last_reauth:.2f} seconds before re-authenticating")
                                await asyncio.sleep(min_reauth_interval - time_since_last_reauth)
                            last_reauth_time = time.time()
                            break
                        
                        # Fetch updated statistics
                        response = payload_4(session, csrf_token, from_date, to_date)
                        logger.debug(f"Payload 4 response status: {response.status_code}")
                        new_ranges = parse_statistics(response.text)
                        new_ranges_dict = {r["range_name"]: r for r in new_ranges}
                        
                        # Process ranges
                        for range_data in new_ranges:
                            range_name = range_data["range_name"]
                            current_count = range_data["count"]
                            existing_range = existing_ranges_dict.get(range_name)
                            
                            response = payload_5(session, csrf_token, to_date, range_name)
                            logger.debug(f"Payload 5 response status: {response.status_code}")
                            numbers = parse_numbers(response.text)
                            
                            # Initialize number tracking for this range
                            if range_name not in number_tracker:
                                number_tracker[range_name] = {}
                            
                            # Process new numbers or updated counts
                            for number_data in numbers:
                                number = number_data["number"]
                                number_id = number_data["number_id"]
                                
                                # Fetch all messages for this number
                                response = payload_6(session, csrf_token, to_date, number, range_name)
                                logger.debug(f"Payload 6 response status: {response.status_code}")
                                messages = parse_message(response.text)
                                
                                # Initialize number in tracker if not present
                                if number not in number_tracker[range_name]:
                                    number_tracker[range_name][number] = {
                                        "number_id": number_id,
                                        "message_count": 0,
                                        "last_messages": []
                                    }
                                
                                # Check for new or multiple messages
                                current_message_count = len(messages)
                                tracked_message_count = number_tracker[range_name][number]["message_count"]
                                
                                if current_message_count > tracked_message_count:
                                    new_messages = messages[:current_message_count - tracked_message_count]
                                    for msg_data in new_messages[::-1]:
                                        sms = {
                                            "timestamp": msg_data["timestamp"],
                                            "number": number,
                                            "message": msg_data["message"],
                                            "range": range_name,
                                            "revenue": msg_data["revenue"]
                                        }
                                        logger.info(f"New SMS: {sms}")
                                        await send_to_telegram(sms)
                                    
                                    number_tracker[range_name][number]["message_count"] = current_message_count
                                    number_tracker[range_name][number]["last_messages"] = [msg["message"] for msg in messages]
                            
                            # Update range data
                            if not existing_range:
                                logger.info(f"New range detected: {range_name}")
                                existing_ranges.append(range_data)
                                existing_ranges_dict[range_name] = range_data
                            elif current_count != existing_range["count"]:
                                logger.info(f"Count updated for {range_name}: {existing_range['count']} -> {current_count}")
                                for r in existing_ranges:
                                    if r["range_name"] == range_name:
                                        r["count"] = current_count
                                        r["paid"] = range_data["paid"]
                                        r["unpaid"] = range_data["unpaid"]
                                        r["revenue"] = range_data["revenue"]
                                        break
                                existing_ranges_dict[range_name] = range_data
                        
                        # Update storage
                        existing_ranges = new_ranges
                        existing_ranges_dict = new_ranges_dict
                        save_to_json(existing_ranges, JSON_FILE)
                        save_to_json(number_tracker, NUMBER_TRACKER_FILE)
                        
                        await asyncio.sleep(2 + (time.time() % 1))
                    
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}. Response content: {getattr(e, 'response', 'No response')}")
                retry_delay = min(30 * 2 ** min(3, 1), 300)
                logger.info(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
    
    except Exception as e:
        logger.error(f"Main loop failed: {str(e)}")
        raise

if __name__ == "__main__":
    asyncio.run(main())