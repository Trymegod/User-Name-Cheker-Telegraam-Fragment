import aiohttp
import asyncio
from bs4 import BeautifulSoup
import logging
from colorama import init, Fore, Style
import re
from tqdm.asyncio import tqdm
import aiofiles
import sys
import os
import random
from aiohttp import ClientError, ClientResponseError
from collections import Counter

init(autoreset=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

BASE_URL = 'https://fragment.com'
USERNAMES_FILE = 'usernames.txt'
VALID_OUTPUT_FILE = 'valid.txt'
NONVALID_OUTPUT_FILE = 'nonvalid.txt'

MAX_CONCURRENT_REQUESTS = 20 # МАКСИМАЛЬНОЕ КОЛЛИЧЕСТВО ЗАПРОСОВ НА СЕРВЕР
CHUNK_SIZE = 20 # ЗА 1 ЧАНК БУДЕТ ОБРАБАТЫВАТЬСЯ
PAUSE_BETWEEN_CHUNKS = 4 # ПАУЗА МЕЖДУ ЧАНКАМИ 

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[mK]')

def remove_ansi_codes(text):
    return ANSI_ESCAPE.sub('', text)

async def fetch_user_page(session, username):
    user_url = f'{BASE_URL}/username/{username}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' \
                      'AppleWebKit/537.36 (KHTML, like Gecko) ' \
                      'Chrome/58.0.3029.110 Safari/537.3'
    }
    async with session.get(user_url, headers=headers, timeout=10) as response:
        response.raise_for_status()
        return await response.text()

def parse_user_status(html_content, username):
    soup = BeautifulSoup(html_content, 'html.parser')
    og_description = soup.find('meta', {'property': 'og:description'})
    if og_description:
        description = og_description.get('content', '').lower()
        if 'is taken' in description:
            return f"{username} is taken"
        if 'buy' in description or 'make an offer' in description:
            return f"{username} is for sale"
        if 'find active auctions' in description or 'available' in description:
            return f"{username} is not taken"
    if soup.find('div', {'class': 'captcha'}) or "captcha" in html_content.lower():
        return f"{username}: CAPTCHA detected"
    return f"{username}: Unable to determine status"

async def get_user(session, username, semaphore, retries=3):
    async with semaphore:
        for attempt in range(1, retries + 1):
            try:
                await asyncio.sleep(random.uniform(0.1, 0.3))
                html_content = await fetch_user_page(session, username)
                result = parse_user_status(html_content, username)
                if "CAPTCHA detected" in result:
                    logger.warning(f"CAPTCHA detected for {username}.")
                return result
            except ClientResponseError as e:
                if e.status == 429:
                    logger.warning(f"Too Many Requests for {username}. Attempt {attempt}/{retries}.")
                else:
                    logger.warning(f"HTTP error for {username}: {e.status}. Attempt {attempt}/{retries}.")
            except ClientError as e:
                logger.warning(f"Client error for {username}: {e}. Attempt {attempt}/{retries}.")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout error for {username}. Attempt {attempt}/{retries}.")
            except Exception as e:
                logger.error(f"Unexpected error for {username}: {e}.")
                return f"{username}: Unexpected Error ({e})"

            if attempt < retries:
                backoff = 2 ** attempt + random.uniform(0, 1)
                logger.info(f"Waiting for {backoff:.2f} seconds before retrying...")
                await asyncio.sleep(backoff)
            else:
                logger.error(f"Failed to fetch {username} after {retries} attempts.")
                return f"{username}: Error after {retries} attempts"

async def process_usernames(session, usernames, semaphore):
    tasks = [
        get_user(session, username, semaphore)
        for username in usernames
    ]
    return await asyncio.gather(*tasks)

async def main():
    print(Fore.RED + '''

  _______  _____ __     __ __  __  ______  _____   ____   _____  
 |__   __||  __ \\ \   / /|  \/  ||  ____|/ ____| / __ \ |  __ \ 
    | |   | |__) |\ \_/ / | \  / || |__  | |  __ | |  | || |  | |
    | |   |  _  /  \   /  | |\/| ||  __| | | |_ || |  | || |  | |
    | |   | | \ \   | |   | |  | || |____| |__| || |__| || |__| |
    |_|   |_|  \_\  |_|   |_|  |_||______|\_____| \____/ |_____/ 
                                                                 
                                                                                        
                    - Username Checker - 
                     - By @trymegod -
                      - i miss u( -
    ''')
    print(Fore.GREEN + "--------------------------")
    print(Fore.GREEN + "  Для начала проверки нажмите Enter.")
    print(Fore.GREEN + "--------------------------")
    input()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    usernames_file = os.path.join(script_dir, USERNAMES_FILE)

    if not os.path.isfile(usernames_file):
        logger.error(f"Файл {usernames_file} не найден.")
        sys.exit(1)

    try:
        async with aiofiles.open(usernames_file, 'r', encoding='utf-8') as f:
            contents = await f.readlines()
            usernames = [line.strip() for line in contents if line.strip()]
        logger.info(f"Юзеры из файла: {len(usernames)}")
    except Exception as e:
        logger.error(f"Ошибка при чтении файла: {e}")
        sys.exit(1)

    unique_usernames = list(set(usernames))
    logger.info(f"Юзеров для проверки: {len(unique_usernames)}")

    username_counts = Counter(usernames)
    duplicates = {username: count for username, count in username_counts.items() if count > 1}
    if duplicates:
        logger.info(f"Найдено {len(duplicates)} дубликатов юзеров.")
    else:
        logger.info("Ничего не повторяеться")

    async with aiohttp.ClientSession() as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        total_chunks = (len(unique_usernames) + CHUNK_SIZE - 1) // CHUNK_SIZE

        async with aiofiles.open(VALID_OUTPUT_FILE, 'w', encoding='utf-8') as valid_file, \
                   aiofiles.open(NONVALID_OUTPUT_FILE, 'w', encoding='utf-8') as nonvalid_file:

            progress_bar = tqdm(
                total=total_chunks,
                desc="Processing chunks",
                position=0,
                leave=True
            )

            chunk_info = tqdm(
                total=1,
                desc="Current Chunk: None",
                position=1,
                bar_format='{desc}',
                leave=True
            )

            valid_display = tqdm(
                total=0,
                desc="Valid Usernames",
                position=2,
                bar_format='{desc}',
                leave=True
            )

            for chunk_number, i in enumerate(range(0, len(unique_usernames), CHUNK_SIZE), 1):
                chunk = unique_usernames[i:i+CHUNK_SIZE]
                logger.info(f"Processing chunk {chunk_number}/{total_chunks}...")

                chunk_info.set_description(f"Current Chunk: {chunk_number}/{total_chunks}")

                chunk_results = await process_usernames(session, chunk, semaphore)

                for result in chunk_results:
                    cleaned_result = remove_ansi_codes(result)
                    if "is not taken" in result:
                        valid_username = result.split(" is not taken")[0]
                        await valid_file.write(valid_username + '\n')
                        valid_display.write(Fore.GREEN + valid_username)
                        valid_display.refresh()
                    elif "is taken" in result or "Unable to determine status" in result:
                        nonvalid_username = result.split(" is ")[0]
                        await nonvalid_file.write(nonvalid_username + '\n')
                    elif "CAPTCHA detected" in result or "Error" in result:
                        print(Fore.RED + result)

                progress_bar.update(1)

                if chunk_number < total_chunks:
                    pause_duration = PAUSE_BETWEEN_CHUNKS + random.uniform(0, 2)
                    logger.info(f"Pausing for {pause_duration:.2f} seconds...")
                    await asyncio.sleep(pause_duration)

            chunk_info.close()
            valid_display.close()
            progress_bar.close()

    logger.info("Поздравляю! Проверь файл Valid.txt")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.warning("Вы остановили подбор")
    except Exception as e:
        logger.error(f"Error {e}")
