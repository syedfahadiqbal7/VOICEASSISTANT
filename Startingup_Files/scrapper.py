import requests
from bs4 import BeautifulSoup

def scrape_website(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Example: Extract all the headers
    headers = soup.find_all(['h1', 'h2', 'h3'])
    for header in headers:
        print(header.text)

if __name__ == '__main__':
    url = 'https://example.com'
    scrape_website(url)
