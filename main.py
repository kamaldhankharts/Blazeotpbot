import os
import json
import logging
import time
import requests
import re
import urllib.parse
import asyncio
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_fixed


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(user_id)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Common headers
BASE_HEADERS = {
    "Host": "www.ivasms.com",
    "Cache-Control": "max-age=0",
    "Sec-Ch-Ua": '"Not)A;Brand";v="8", "Chromium";v="138"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Accept-Language": "en-GB,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "Accept-Encoding": "gzip, deflate, br",
    "Priority": "u=0, i",
    "Connection": "keep-alive"
}

# Google Sheets setup
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SHEET_ID = "1wDmJeXmWA7BHSsap5LRcEy62pm_xvqqGI8G3Xfo22JQ"
SHEETS = {
    "admins": "Admins",
    "approved_users": "ApprovedUsers",
    "banned_users": "BannedUsers",
    "range_assignments": "RangeAssignments"
}
SUPER_ADMIN_ID = "5297298247"

# ConversationHandler states
CONFIRM_DELETE, CANCEL = range(2)

def get_sheets_service():
    """Initialize Google Sheets API service using service account."""
    try:
        credentials_json = os.getenv("GOOGLE_CREDENTIALS")
        if not credentials_json:
            raise ValueError("GOOGLE_CREDENTIALS environment variable not set")
        
        try:
            credentials_info = json.loads(credentials_json)
        except json.JSONDecodeError:
            with open(credentials_json, 'r') as f:
                credentials_info = json.load(f)
        
        credentials = Credentials.from_service_account_info(credentials_info, scopes=SCOPES)
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        logger.error(f"Failed to initialize Google Sheets service: {str(e)}", extra={"user_id": "N/A"})
        raise

def initialize_sheets():
    """Initialize required sheets if they don't exist."""
    service = get_sheets_service()
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=SHEET_ID).execute()
        existing_sheets = [sheet['properties']['title'] for sheet in spreadsheet['sheets']]
        
        for sheet_name in SHEETS.values():
            if sheet_name not in existing_sheets:
                batch_update_request = {
                    "requests": [{
                        "addSheet": {
                            "properties": {
                                "title": sheet_name
                            }
                        }
                    }]
                }
                service.spreadsheets().batchUpdate(
                    spreadsheetId=SHEET_ID,
                    body=batch_update_request
                ).execute()
                logger.info(f"Created sheet: {sheet_name}", extra={"user_id": "N/A"})
                
                headers = {
                    SHEETS["admins"]: [["UserID"]],
                    SHEETS["approved_users"]: [["UserID"]],
                    SHEETS["banned_users"]: [["UserID"]],
                    SHEETS["range_assignments"]: [["UserID", "RangeName", "TerminationID", "AddedAt"]]
                }
                service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"{sheet_name}!A1",
                    valueInputOption="RAW",
                    body={"values": headers[sheet_name]}
                ).execute()
                logger.info(f"Initialized headers for sheet: {sheet_name}", extra={"user_id": "N/A"})
    except HttpError as e:
        logger.error(f"Failed to initialize sheets: {str(e)}", extra={"user_id": "N/A"})
        raise

def get_user_data(sheet_name):
    """Retrieve user data from a specific sheet."""
    service = get_sheets_service()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"{sheet_name}!A2:A"
        ).execute()
        return [row[0] for row in result.get('values', [])]
    except HttpError as e:
        logger.error(f"Failed to get data from {sheet_name}: {str(e)}", extra={"user_id": "N/A"})
        return []

def get_range_assignments():
    """Retrieve range assignments from the sheet."""
    service = get_sheets_service()
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"{SHEETS['range_assignments']}!A2:D"
        ).execute()
        assignments = []
        for row in result.get('values', []):
            if len(row) >= 3:
                assignments.append({
                    "user_id": row[0],
                    "range_name": row[1],
                    "termination_id": row[2],
                    "added_at": row[3] if len(row) > 3 else ""
                })
        return assignments
    except HttpError as e:
        logger.error(f"Failed to get range assignments: {str(e)}", extra={"user_id": "N/A"})
        return []

def update_range_assignment(user_id, range_name, termination_id):
    """Update range assignment in the sheet."""
    service = get_sheets_service()
    try:
        assignments = get_range_assignments()
        row_index = len(assignments) + 2
        service.spreadsheets().values().update(
            spreadsheetId=SHEET_ID,
            range=f"{SHEETS['range_assignments']}!A{row_index}:D{row_index}",
            valueInputOption="RAW",
            body={"values": [[user_id, range_name, termination_id, datetime.now().isoformat()]]}
        ).execute()
        logger.info(f"Updated range assignment for user {user_id}: {range_name}", extra={"user_id": user_id})
    except HttpError as e:
        logger.error(f"Failed to update range assignment: {str(e)}", extra={"user_id": user_id})
        raise

def add_user_to_sheet(sheet_name, user_id):
    """Add a user to the specified sheet."""
    service = get_sheets_service()
    try:
        users = get_user_data(sheet_name)
        if user_id not in users:
            row_index = len(users) + 2
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"{sheet_name}!A{row_index}",
                valueInputOption="RAW",
                body={"values": [[user_id]]}
            ).execute()
            logger.info(f"Added user {user_id} to {sheet_name}", extra={"user_id": user_id})
        return True
    except HttpError as e:
        logger.error(f"Failed to add user {user_id} to {sheet_name}: {str(e)}", extra={"user_id": user_id})
        return False

def remove_user_from_sheet(sheet_name, user_id):
    """Remove a user from the specified sheet."""
    service = get_sheets_service()
    try:
        users = get_user_data(sheet_name)
        if user_id in users:
            row_index = users.index(user_id) + 2
            service.spreadsheets().values().clear(
                spreadsheetId=SHEET_ID,
                range=f"{sheet_name}!A{row_index}"
            ).execute()
            logger.info(f"Removed user {user_id} from {sheet_name}", extra={"user_id": user_id})
            return True
        return False
    except HttpError as e:
        logger.error(f"Failed to remove user {user_id} from {sheet_name}: {str(e)}", extra={"user_id": user_id})
        return False

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
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
        logger.info(f"Extracted CSRF token: {token_match.group(1)}", extra={"user_id": "N/A"})
        return {"_token": token_match.group(1)}
    except Exception as e:
        logger.error(f"Payload 1 failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
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
        logger.info(f"Login response status: {response.status_code}, URL: {response.url}", extra={"user_id": "N/A"})
        if response.url.endswith("/login"):
            raise ValueError("Login failed, redirected back to /login")
        return response
    except Exception as e:
        logger.error(f"Payload 2 failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_check(session):
    """Send GET request to /portal/sms/test/sms to get available ranges."""
    url = f"https://www.ivasms.com/portal/sms/test/sms?draw=1&columns%5B0%5D%5Bdata%5D=range&columns%5B0%5D%5Borderable%5D=false&columns%5B1%5D%5Bdata%5D=termination.test_number&columns%5B1%5D%5Bsearchable%5D=false&columns%5B1%5D%5Borderable%5D=false&columns%5B2%5D%5Bdata%5D=originator&columns%5B2%5D%5Borderable%5D=false&columns%5B3%5D%5Bdata%5D=messagedata&columns%5B3%5D%5Borderable%5D=false&columns%5B4%5D%5Bdata%5D=senttime&columns%5B4%5D%5Bsearchable%5D=false&order%5B0%5D%5Bcolumn%5D=4&order%5B0%5D%5Bdir%5D=desc&start=0&length=25&search%5Bvalue%5D=&_={int(time.time() * 1000)}"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/sms/test/sms"
    })
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload check failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_active(session):
    """Send GET request to /portal/live/my_sms to get active SMS data."""
    url = "https://www.ivasms.com/portal/live/my_sms"
    headers = BASE_HEADERS.copy()
    headers.update({
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Referer": "https://www.ivasms.com/portal"
    })
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Payload active failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_10(session, range_name):
    """Search for a range to get termination ID."""
    url = f"https://www.ivasms.com/portal/numbers/test?draw=2&columns%5B0%5D%5Bdata%5D=range&columns%5B1%5D%5Bdata%5D=test_number&columns%5B2%5D%5Bdata%5D=term&columns%5B3%5D%5Bdata%5D=P2P&columns%5B4%5D%5Bdata%5D=A2P&columns%5B5%5D%5Bdata%5D=Limit_Range&columns%5B6%5D%5Bdata%5D=limit_cli_a2p&columns%5B7%5D%5Bdata%5D=limit_did_a2p&columns%5B8%5D%5Bdata%5D=limit_cli_did_a2p&columns%5B9%5D%5Bdata%5D=limit_cli_p2p&columns%5B10%5D%5Bdata%5D=limit_did_p2p&columns%5B11%5D%5Bdata%5D=limit_cli_did_p2p&columns%5B12%5D%5Bdata%5D=updated_at&columns%5B13%5D%5Bdata%5D=action&columns%5B13%5D%5Bsearchable%5D=false&columns%5B13%5D%5Borderable%5D=false&order%5B0%5D%5Bcolumn%5D=1&order%5B0%5D%5Bdir%5D=desc&start=0&length=50&search%5Bvalue%5D={urllib.parse.quote(range_name)}&_=1754468451369"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Csrf-Token": session.csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers/test"
    })
    
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload 10 failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_11(session, termination_id, csrf_token):
    """Get termination details."""
    url = "https://www.ivasms.com/portal/numbers/termination/details"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "text/html, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.ivasms.com",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers/test"
    })
    
    data = {"id": termination_id, "_token": csrf_token}
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"Payload 11 failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_12(session, termination_id, csrf_token):
    """Add number to termination."""
    url = "https://www.ivasms.com/portal/numbers/termination/number/add"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Csrf-Token": csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.ivasms.com",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers/test"
    })
    
    data = {"_token": csrf_token, "id": termination_id}
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload 12 failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_13(session, termination_id, csrf_token):
    """Get numbers for a range."""
    url = "https://www.ivasms.com/portal/live/getNumbers"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.ivasms.com",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/live/my_sms"
    })
    
    data = {"termination_id": termination_id, "_token": csrf_token}
    
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload 13 failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_search_numbers(session, range_name):
    """Search for numbers in a specific range."""
    url = f"https://www.ivasms.com/portal/numbers?draw=1&columns%5B0%5D%5Bdata%5D=number_id&columns%5B0%5D%5Bname%5D=id&columns%5B0%5D%5Borderable%5D=false&columns%5B1%5D%5Bdata%5D=Number&columns%5B2%5D%5Bdata%5D=range&columns%5B3%5D%5Bdata%5D=A2P&columns%5B4%5D%5Bdata%5D=P2P&columns%5B5%5D%5Bdata%5D=LimitA2P&columns%5B6%5D%5Bdata%5D=limit_cli_a2p&columns%5B7%5D%5Bdata%5D=limit_did_a2p&columns%5B8%5D%5Bdata%5D=limit_cli_did_a2p&columns%5B9%5D%5Bdata%5D=LimitP2P&columns%5B10%5D%5Bdata%5D=limit_cli_p2p&columns%5B11%5D%5Bdata%5D=limit_did_p2p&columns%5B12%5D%5Bdata%5D=limit_cli_did_p2p&columns%5B13%5D%5Bdata%5D=action&columns%5B13%5D%5Bsearchable%5D=false&columns%5B13%5D%5Borderable%5D=false&order%5B0%5D%5Bcolumn%5D=1&order%5B0%5D%5Bdir%5D=desc&start=0&length=100&search%5Bvalue%5D={urllib.parse.quote(range_name)}&_=1754654048583"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Csrf-Token": session.csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers"
    })
    try:
        response = session.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        numbers = [
            {
                "number_id": re.search(r'value="(\d+)"', item["number_id"]).group(1) if re.search(r'value="(\d+)"', item["number_id"]) else None,
                "number": item["Number"],
                "range": item["range"]
            }
            for item in data.get("data", [])
            if item["number_id"] and item["Number"] and item["range"]
        ]
        return {"total": data.get("recordsFiltered", 0), "numbers": numbers}
    except Exception as e:
        logger.error(f"Payload search numbers failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_delete_numbers(session, number_ids):
    """Delete multiple numbers from a range using bulk delete."""
    url = "https://www.ivasms.com/portal/numbers/return/number/bluck"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Csrf-Token": session.csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.ivasms.com",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers"
    })
    data = {"NumberID[]": number_ids}
    try:
        response = session.post(url, headers=headers, data=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload delete numbers failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def payload_delete_all(session):
    """Delete all numbers in the panel."""
    url = "https://www.ivasms.com/portal/numbers/return/allnumber/bluck"
    headers = BASE_HEADERS.copy()
    headers.update({
        "X-Csrf-Token": session.csrf_token,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://www.ivasms.com",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": "https://www.ivasms.com/portal/numbers"
    })
    try:
        response = session.post(url, headers=headers, data={}, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Payload delete all failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

def parse_ranges(response_json):
    """Parse available ranges from JSON response."""
    try:
        ranges = []
        for item in response_json.get('data', []):
            range_name = item.get('range', '')
            termination_id = item.get('id', '')
            if range_name and termination_id:
                ranges.append({"range_name": range_name, "termination_id": str(termination_id)})
        return ranges
    except Exception as e:
        logger.error(f"Parse ranges failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        return []

def parse_active_data(response_text):
    """Parse active SMS data from /portal/live/my_sms response."""
    try:
        soup = BeautifulSoup(response_text, 'html.parser')
        active_data = {"ranges": [], "total_numbers": 0}
        
        # Extract ranges from accordion
        accordion = soup.find('div', id='accordion')
        if accordion:
            range_cards = accordion.find_all('div', class_='card card-secondary')
            for card in range_cards:
                range_link = card.find('a', class_=re.compile(r'd-block w-100'))
                if range_link:
                    range_name = range_link.text.strip()
                    termination_id = range_link.get('data-id', '')
                    if range_name and termination_id:
                        active_data["ranges"].append({"range_name": range_name, "termination_id": termination_id})
        
        # Extract total numbers
        total_numbers_header = soup.find('h6', class_='mb-0', string=re.compile(r'My Numbers'))
        if total_numbers_header:
            total_numbers_match = re.search(r'\((\d+)\)', total_numbers_header.text)
            if total_numbers_match:
                active_data["total_numbers"] = int(total_numbers_match.group(1))
        
        return active_data
    except Exception as e:
        logger.error(f"Parse active data failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

async def send_to_telegram(chat_id, message, user_id="N/A"):
    """Send message to Telegram with pagination."""
    bot = Bot(token=os.getenv("BOT_TOKEN"))
    try:
        messages = []
        current_message = ""
        for line in message.split("\n"):
            if len(current_message) + len(line) + 1 > 4096:
                messages.append(current_message)
                current_message = line + "\n"
            else:
                current_message += line + "\n"
        if current_message:
            messages.append(current_message)
        
        for msg in messages:
            await bot.send_message(chat_id=chat_id, text=msg.strip(), parse_mode="Markdown")
            await asyncio.sleep(0.5)
        logger.info(f"Sent message to chat {chat_id}", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Failed to send to Telegram: {str(e)}", exc_info=True, extra={"user_id": user_id})

async def check_user_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check if user is authorized to use the bot."""
    user_id = str(update.effective_user.id)
    banned_users = get_user_data(SHEETS["banned_users"])
    if user_id in banned_users:
        await update.message.reply_text("You are banned from using this bot.")
        logger.info(f"User {user_id} is banned", extra={"user_id": user_id})
        return False
    approved_users = get_user_data(SHEETS["approved_users"])
    if user_id not in approved_users:
        await update.message.reply_text(
            "You are not an approved user. Use `/request` to request approval from the super admin."
        )
        logger.info(f"User {user_id} is not approved", extra={"user_id": user_id})
        return False
    return True

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command to display bot usage."""
    user_id = str(update.effective_user.id)
    admins = get_user_data(SHEETS["admins"])
    is_admin = user_id in admins or user_id == SUPER_ADMIN_ID
    message = (
        "Welcome to the IVASMS Bot!\n\n"
        "Available commands:\n"
        "- `/request`: Request approval to use the bot.\n"
        "- `/check`: List all active ranges in the panel.\n"
        "- `/add <range_name>`: Add a new range to the panel.\n"
        "- `/delete <range_name>`: Delete a range from the panel.\n"
        "- `/view <range_name>`: View numbers in a specific range.\n"
    )
    if is_admin:
        message += (
            "- `/approve <user_id>`: Approve a user (admin only).\n"
            "- `/unapprove <user_id>`: Remove approval from a user (admin only).\n"
            "- `/ban <user_id>`: Ban a user (admin only).\n"
            "- `/deleteall`: Delete all ranges from the panel (admin only).\n"
            "- `/active`: List all active ranges in the panel (admin only).\n"
        )
    message += "\nYou must be an approved user to use most commands. Use `/request` to get approved."
    await send_to_telegram(update.effective_chat.id, message, user_id)

async def request_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /request command for users to request approval."""
    user_id = str(update.effective_user.id)
    username = update.effective_user.username or "No username"
    banned_users = get_user_data(SHEETS["banned_users"])
    approved_users = get_user_data(SHEETS["approved_users"])
    
    if user_id in banned_users:
        await update.message.reply_text("You are banned and cannot request approval.")
        logger.info(f"Banned user {user_id} attempted to request approval", extra={"user_id": user_id})
        return
    if user_id in approved_users:
        await update.message.reply_text("You are already an approved user.")
        logger.info(f"Approved user {user_id} requested approval", extra={"user_id": user_id})
        return
    
    try:
        message = f"Approval request from user ID: `{user_id}`\nUsername: @{username}\nUse `/approve {user_id}` to approve or `/ban {user_id}` to ban."
        await send_to_telegram(SUPER_ADMIN_ID, message, user_id)
        await update.message.reply_text("Your approval request has been sent to the super admin.")
        logger.info(f"User {user_id} requested approval", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Failed to send approval request for {user_id}: {str(e)}", extra={"user_id": user_id})
        await update.message.reply_text(f"Error sending approval request: {str(e)}")

async def approve_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /approve <user_id> command for admins."""
    user_id = str(update.effective_user.id)
    admins = get_user_data(SHEETS["admins"])
    if user_id not in admins and user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("Only admins can use the /approve command.")
        logger.info(f"Non-admin {user_id} attempted /approve", extra={"user_id": user_id})
        return
    
    if not context.args:
        await update.message.reply_text("Please provide a user ID: `/approve <user_id>`", parse_mode="Markdown")
        return
    
    target_user_id = context.args[0]
    if add_user_to_sheet(SHEETS["approved_users"], target_user_id):
        await update.message.reply_text(f"User `{target_user_id}` approved successfully.")
        await send_to_telegram(target_user_id, "You have been approved to use the IVASMS bot!", target_user_id)
        logger.info(f"User {target_user_id} approved by {user_id}", extra={"user_id": user_id})
    else:
        await update.message.reply_text(f"Failed to approve user `{target_user_id}`. Check logs for details.")
        logger.error(f"Failed to approve user {target_user_id}", extra={"user_id": user_id})

async def unapprove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unapprove <user_id> command for admins."""
    user_id = str(update.effective_user.id)
    admins = get_user_data(SHEETS["admins"])
    if user_id not in admins and user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("Only admins can use the /unapprove command.")
        logger.info(f"Non-admin {user_id} attempted /unapprove", extra={"user_id": user_id})
        return
    
    if not context.args:
        await update.message.reply_text("Please provide a user ID: `/unapprove <user_id>`", parse_mode="Markdown")
        return
    
    target_user_id = context.args[0]
    if remove_user_from_sheet(SHEETS["approved_users"], target_user_id):
        await update.message.reply_text(f"User `{target_user_id}` unapproved successfully.")
        await send_to_telegram(target_user_id, "Your approval to use the IVASMS bot has been revoked.", target_user_id)
        logger.info(f"User {target_user_id} unapproved by {user_id}", extra={"user_id": user_id})
    else:
        await update.message.reply_text(f"User `{target_user_id}` was not approved or could not be unapproved.")
        logger.info(f"User {target_user_id} not found in approved users", extra={"user_id": user_id})

async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ban <user_id> command for admins."""
    user_id = str(update.effective_user.id)
    admins = get_user_data(SHEETS["admins"])
    if user_id not in admins and user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("Only admins can use the /ban command.")
        logger.info(f"Non-admin {user_id} attempted /ban", extra={"user_id": user_id})
        return
    
    if not context.args:
        await update.message.reply_text("Please provide a user ID: `/ban <user_id>`", parse_mode="Markdown")
        return
    
    target_user_id = context.args[0]
    if add_user_to_sheet(SHEETS["banned_users"], target_user_id):
        remove_user_from_sheet(SHEETS["approved_users"], target_user_id)
        await update.message.reply_text(f"User `{target_user_id}` banned successfully.")
        await send_to_telegram(target_user_id, "You have been banned from using the IVASMS bot.", target_user_id)
        logger.info(f"User {target_user_id} banned by {user_id}", extra={"user_id": user_id})
    else:
        await update.message.reply_text(f"Failed to ban user `{target_user_id}`. Check logs for details.")
        logger.error(f"Failed to ban user {target_user_id}", extra={"user_id": user_id})

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /check command to list active ranges."""
    user_id = str(update.effective_user.id)
    if not await check_user_permissions(update, context):
        return

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            response = payload_check(session)
            with open("check_response.json", "w", encoding="utf-8") as f:
                json.dump(response, f, ensure_ascii=False, indent=2)

            ranges = parse_ranges(response)
            if not ranges:
                await update.message.reply_text("No active ranges found in the panel.")
                logger.info(f"User {user_id} checked active ranges: none found", extra={"user_id": user_id})
                return

            range_list = [f"`{r['range_name']}` (ID: `{r['termination_id']}`)" for r in ranges]
            message = f"Active ranges ({len(ranges)} ranges):\n" + "\n".join(range_list)
            await send_to_telegram(update.effective_chat.id, message, user_id)
            logger.info(f"User {user_id} checked active ranges", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Check command failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error retrieving active ranges: {str(e)}")

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add <range_name> command with confirmation for existing range."""
    user_id = str(update.effective_user.id)
    if not await check_user_permissions(update, context):
        return

    range_name = " ".join(context.args).strip() if context.args else ""
    if not range_name:
        await update.message.reply_text("Please provide a range name: `/add <range_name>`", parse_mode="Markdown")
        logger.info(f"User {user_id} used /add without range name", extra={"user_id": user_id})
        return

    admins = get_user_data(SHEETS["admins"])
    is_admin = user_id in admins or user_id == SUPER_ADMIN_ID
    assignments = get_range_assignments()
    user_assignment = next((a for a in assignments if a["user_id"] == user_id), None)

    if not is_admin and user_assignment:
        context.user_data["new_range"] = range_name
        await update.message.reply_text(
            f"You already have an active range: `{user_assignment['range_name']}`. "
            "Delete it to add a new one? Reply with `/confirm_delete` or `/cancel`.",
            parse_mode="Markdown"
        )
        logger.info(f"User {user_id} has existing range {user_assignment['range_name']}", extra={"user_id": user_id})
        return CONFIRM_DELETE

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            response = payload_active(session)
            active_data = parse_active_data(response.text)
            if active_data["total_numbers"] >= 1000:
                await update.message.reply_text("Cannot add range: Panel has reached the 1000-number limit.")
                logger.info(f"User {user_id} attempted to add range but panel is full", extra={"user_id": user_id})
                return

            response = payload_10(session, range_name)
            ranges = parse_ranges(response)
            matching_range = next((r for r in ranges if r["range_name"].lower() == range_name.lower()), None)

            if not matching_range:
                await update.message.reply_text(f"Range `{range_name}` not found. Please check the range name and try again.")
                logger.info(f"User {user_id} attempted to add non-existent range {range_name}", extra={"user_id": user_id})
                return

            termination_id = matching_range["termination_id"]
            payload_11(session, termination_id, session.csrf_token)
            response = payload_12(session, termination_id, session.csrf_token)

            if response.get("message", "").startswith("done add number"):
                update_range_assignment(user_id, range_name, termination_id)
                numbers = payload_13(session, termination_id, session.csrf_token)
                number_list = [f"`+{num['Number']}`" for num in numbers]
                message = f"Range `{range_name}` added successfully!\n\nNumbers:\n" + "\n".join(number_list)
                await send_to_telegram(update.effective_chat.id, message, user_id)
                logger.info(f"User {user_id} added range {range_name}", extra={"user_id": user_id})
            else:
                await update.message.reply_text(f"Failed to add range `{range_name}`: {response.get('message', 'Unknown error')}")
                logger.error(f"Failed to add range {range_name}: {response.get('message', 'Unknown error')}", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Add command failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error adding range `{range_name}`: {str(e)}")
    return ConversationHandler.END

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /delete <range_name> command."""
    user_id = str(update.effective_user.id)
    if not await check_user_permissions(update, context):
        return

    range_name = " ".join(context.args).strip() if context.args else ""
    if not range_name:
        await update.message.reply_text("Please provide a range name: `/delete <range_name>`", parse_mode="Markdown")
        logger.info(f"User {user_id} used /delete without range name", extra={"user_id": user_id})
        return

    admins = get_user_data(SHEETS["admins"])
    is_admin = user_id in admins or user_id == SUPER_ADMIN_ID
    assignments = get_range_assignments()
    user_assignment = next((a for a in assignments if a["range_name"].lower() == range_name.lower()), None)

    if not is_admin and not user_assignment:
        await update.message.reply_text(f"You don't have an active range named `{range_name}`.")
        logger.info(f"User {user_id} attempted to delete non-existent range {range_name}", extra={"user_id": user_id})
        return
    if not is_admin and user_assignment["user_id"] != user_id:
        await update.message.reply_text("You can only delete your own range.")
        logger.info(f"User {user_id} attempted to delete range {range_name} not owned", extra={"user_id": user_id})
        return

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            search_result = payload_search_numbers(session, range_name)
            if not search_result["numbers"]:
                await update.message.reply_text(f"No numbers found for range `{range_name}` in the panel. It may have been removed already.")
                logger.info(f"No numbers found for range {range_name} by user {user_id}", extra={"user_id": user_id})
                return

            number_ids = [num["number_id"] for num in search_result["numbers"] if num["number_id"]]
            if not number_ids:
                await update.message.reply_text(f"No valid number IDs found for range `{range_name}`. Contact support if this persists.")
                logger.warning(f"No valid number IDs for range {range_name} by user {user_id}", extra={"user_id": user_id})
                return

            response = payload_delete_numbers(session, number_ids)
            if "NumberDoneRemove" in response:
                service = get_sheets_service()
                row_index = next((i + 2 for i, a in enumerate(assignments) if a["range_name"].lower() == range_name.lower() and (is_admin or a["user_id"] == user_id)), None)
                if row_index:
                    service.spreadsheets().values().clear(
                        spreadsheetId=SHEET_ID,
                        range=f"{SHEETS['range_assignments']}!A{row_index}:D{row_index}"
                    ).execute()
                    logger.info(f"Deleted range assignment for {range_name} from sheets by user {user_id}", extra={"user_id": user_id})

                await update.message.reply_text(f"Range `{range_name}` deleted successfully!")
                logger.info(f"User {user_id} deleted range {range_name}", extra={"user_id": user_id})
            else:
                await update.message.reply_text(f"Failed to delete range `{range_name}`: {response.get('message', 'Unknown error')}")
                logger.error(f"Failed to delete range {range_name}: {response.get('message', 'Unknown error')}", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Delete command failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error deleting range `{range_name}`: {str(e)}")

async def delete_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deleteall command for admins."""
    user_id = str(update.effective_user.id)
    admins = get_user_data(SHEETS["admins"])
    if user_id not in admins and user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("Only admins can use the /deleteall command.")
        logger.info(f"Non-admin {user_id} attempted /deleteall", extra={"user_id": user_id})
        return

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            response = payload_delete_all(session)
            if response.get("NumberDoneRemove", []) == ["all numbers"]:
                service = get_sheets_service()
                service.spreadsheets().values().clear(
                    spreadsheetId=SHEET_ID,
                    range=f"{SHEETS['range_assignments']}!A2:D"
                ).execute()
                logger.info(f"Cleared all range assignments from sheets by user {user_id}", extra={"user_id": user_id})
                await update.message.reply_text("All ranges deleted successfully!")
                logger.info(f"User {user_id} deleted all ranges", extra={"user_id": user_id})
            else:
                await update.message.reply_text(f"Failed to delete all ranges: {response.get('message', 'Unknown error')}")
                logger.error(f"Failed to delete all ranges: {response.get('message', 'Unknown error')}", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Delete all command failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error deleting all ranges: {str(e)}")

async def view_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /view <range_name> command."""
    user_id = str(update.effective_user.id)
    if not await check_user_permissions(update, context):
        return

    range_name = " ".join(context.args).strip() if context.args else ""
    if not range_name:
        await update.message.reply_text("Please provide a range name: `/view <range_name>`", parse_mode="Markdown")
        logger.info(f"User {user_id} used /view without range name", extra={"user_id": user_id})
        return

    admins = get_user_data(SHEETS["admins"])
    is_admin = user_id in admins or user_id == SUPER_ADMIN_ID
    assignments = get_range_assignments()
    user_assignment = next((a for a in assignments if a["range_name"].lower() == range_name.lower()), None)

    if not is_admin and not user_assignment:
        await update.message.reply_text(f"You don't have an active range named `{range_name}`.")
        logger.info(f"User {user_id} attempted to view non-existent range {range_name}", extra={"user_id": user_id})
        return
    if not is_admin and user_assignment["user_id"] != user_id:
        await update.message.reply_text("You can only view your own range.")
        logger.info(f"User {user_id} attempted to view range {range_name} not owned", extra={"user_id": user_id})
        return

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            search_result = payload_search_numbers(session, range_name)
            if not search_result["numbers"]:
                message = f"No numbers found for range `{range_name}` in the panel. Possible reasons:\n"
                message += "- The range may have been removed from the panel.\n"
                message += "- The range name may be incorrect (check spelling or case).\n"
                message += "- The IVASMS API may be experiencing issues."
                await update.message.reply_text(message)
                logger.info(f"No numbers found for range {range_name} by user {user_id}", extra={"user_id": user_id})
                return

            number_list = [f"`+{num['number']}`" for num in search_result["numbers"]]
            message = f"Numbers in range `{range_name}` ({search_result['total']}):\n" + "\n".join(number_list)
            await send_to_telegram(update.effective_chat.id, message, user_id)
            logger.info(f"User {user_id} viewed range {range_name}", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"View command failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error viewing range `{range_name}`: {str(e)}")

async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /active command for admins."""
    user_id = str(update.effective_user.id)
    admins = get_user_data(SHEETS["admins"])
    if user_id not in admins and user_id != SUPER_ADMIN_ID:
        await update.message.reply_text("Only admins can use the /active command.")
        logger.info(f"Non-admin {user_id} attempted /active", extra={"user_id": user_id})
        return

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            response = payload_active(session)
            active_data = parse_active_data(response.text)
            if not active_data["ranges"]:
                await update.message.reply_text("No active ranges found in the panel.")
                logger.info(f"User {user_id} checked active ranges: none found", extra={"user_id": user_id})
                return

            range_list = [f"`{r['range_name']}` (ID: `{r['termination_id']}`)" for r in active_data["ranges"]]
            message = f"Active ranges ({active_data['total_numbers']} numbers):\n" + "\n".join(range_list)
            await send_to_telegram(update.effective_chat.id, message, user_id)
            logger.info(f"User {user_id} checked active ranges", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Active command failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error retrieving active ranges: {str(e)}")

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /confirm_delete to delete existing range and add new one."""
    user_id = str(update.effective_user.id)
    new_range = context.user_data.get("new_range")
    if not new_range:
        await update.message.reply_text("No new range specified. Please use `/add <range_name>` again.", parse_mode="Markdown")
        logger.info(f"User {user_id} used /confirm_delete without new range", extra={"user_id": user_id})
        return ConversationHandler.END

    assignments = get_range_assignments()
    user_assignment = next((a for a in assignments if a["user_id"] == user_id), None)
    if not user_assignment:
        await update.message.reply_text("No active range found. Proceeding to add new range.", parse_mode="Markdown")
        context.args = [new_range]
        await add_command(update, context)
        return ConversationHandler.END

    try:
        with requests.Session() as session:
            tokens = payload_1(session)
            payload_2(session, tokens["_token"])
            session.csrf_token = tokens["_token"]

            search_result = payload_search_numbers(session, user_assignment["range_name"])
            number_ids = [num["number_id"] for num in search_result["numbers"] if num["number_id"]]
            if number_ids:
                response = payload_delete_numbers(session, number_ids)
                if "NumberDoneRemove" in response:
                    service = get_sheets_service()
                    row_index = next((i + 2 for i, a in enumerate(assignments) if a["user_id"] == user_id), None)
                    if row_index:
                        service.spreadsheets().values().clear(
                            spreadsheetId=SHEET_ID,
                            range=f"{SHEETS['range_assignments']}!A{row_index}:D{row_index}"
                        ).execute()
                        logger.info(f"Deleted range assignment for {user_assignment['range_name']} from sheets by user {user_id}", extra={"user_id": user_id})

            context.args = [new_range]
            await add_command(update, context)
    except Exception as e:
        logger.error(f"Confirm delete failed: {str(e)}", exc_info=True, extra={"user_id": user_id})
        await update.message.reply_text(f"Error deleting existing range: {str(e)}")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel to abort range addition."""
    user_id = str(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text("Range addition cancelled.", parse_mode="Markdown")
    logger.info(f"User {user_id} cancelled range addition", extra={"user_id": user_id})
    return ConversationHandler.END

async def main():
    """Main function to run the bot."""
    try:
        initialize_sheets()
        application = Application.builder().token(os.getenv("BOT_TOKEN")).build()
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("add", add_command)],
            states={
                CONFIRM_DELETE: [CommandHandler("confirm_delete", confirm_delete)],
                CANCEL: [CommandHandler("cancel", cancel)]
            },
            fallbacks=[CommandHandler("cancel", cancel)]
        )
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("request", request_command))
        application.add_handler(CommandHandler("approve", approve_command))
        application.add_handler(CommandHandler("unapprove", unapprove_command))
        application.add_handler(CommandHandler("ban", ban_command))
        application.add_handler(CommandHandler("check", check_command))
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler("delete", delete_command))
        application.add_handler(CommandHandler("deleteall", delete_all_command))
        application.add_handler(CommandHandler("view", view_command))
        application.add_handler(CommandHandler("active", active_command))
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Telegram bot started", extra={"user_id": "N/A"})

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Main loop failed: {str(e)}", exc_info=True, extra={"user_id": "N/A"})
        raise

if __name__ == "__main__":
    asyncio.run(main())