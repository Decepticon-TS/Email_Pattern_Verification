# email_verifier.py
import os
import re
import time
import random
import requests
import traceback
import pandas as pd
import concurrent.futures
from bs4 import BeautifulSoup
from nameparser import HumanName
from urllib.parse import urlparse
from email_validator import validate_email, EmailNotValidError
from typing import Optional, Dict, Any, Tuple

class EmailVerifier:
    def __init__(self):
        self.errors_log_file = "email_validation_errors.txt"
        self.create_errors_log_file_if_not_exists()
        self.visited_urls = set()
        # self.validate_emails = True
        self.session = requests.Session()
        self.MAX_WORKERS = 5  # Limit concurrent requests
        self.TIMEOUT = 5  # Reduce timeout to 5 seconds
        
    def validate_input_file(self, df: pd.DataFrame) -> Tuple[bool, str]:
        """Validates the input DataFrame structure and content."""
        required_columns = ['Full Name', 'Company URL', 'Pattern']
        
        # Check if DataFrame is empty
        if df.empty:
            return False, "Input file is empty"
            
        # Check for required columns
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            return False, f"Missing required columns: {', '.join(missing_columns)}"
            
        # Check for empty required fields
        empty_fields = []
        for col in required_columns:
            if df[col].isna().any():
                empty_rows = df[df[col].isna()].index.tolist()
                empty_fields.append(f"{col} (rows: {empty_rows})")
        
        if empty_fields:
            return False, f"Empty values found in: {', '.join(empty_fields)}"
            
        return True, "Validation successful"

    def process_dataframe(self, df: pd.DataFrame, callback: Optional[callable] = None) -> pd.DataFrame:
        """Process the DataFrame with improved error handling and validation."""
        try:
            # Validate input file
            is_valid, message = self.validate_input_file(df)
            if not is_valid:
                raise ValueError(f"Input validation failed: {message}")

            # Create copies of input columns to preserve original data
            df = df.copy()
            df['Original Full Name'] = df['Full Name']
            df['Original Company URL'] = df['Company URL']
            df['Original Pattern'] = df['Pattern']

            # Initialize result columns with default values
            df['First Name'] = 'Unknown'
            df['Last Name'] = 'Unknown'
            df['Email'] = ''
            df['Email Verification'] = False
            df['Phone Number'] = ''
            df['Processing Status'] = 'Pending'
            df['Error Message'] = ''

            total_rows = len(df)
            
            for index, row in df.iterrows():
                try:
                    # Update progress
                    progress = int((index + 1) * 100 / total_rows)
                    if callback:
                        callback({'progress': progress, 'status': f'Processing row {index + 1}/{total_rows}'})

                    # Process name
                    names = self.safe_parse_name(row['Full Name'])
                    df.at[index, 'First Name'] = names[0]
                    df.at[index, 'Last Name'] = names[1]

                    # Generate and verify email
                    email = self.safe_generate_email(df.iloc[index])
                    df.at[index, 'Email'] = email
                    df.at[index, 'Email Verification'] = self.verify_email(email)

                    # Extract phone number
                    df.at[index, 'Phone Number'] = self.extract_phone_number(row['Company URL']) or ''

                    df.at[index, 'Processing Status'] = 'Completed'

                except Exception as e:
                    error_msg = f"Error processing row {index}: {str(e)}"
                    self.log_error("RowProcessingError", error_msg)
                    df.at[index, 'Processing Status'] = 'Failed'
                    df.at[index, 'Error Message'] = str(e)

            return df

        except Exception as e:
            error_msg = f"Error processing dataframe: {str(e)}\n{traceback.format_exc()}"
            self.log_error("ProcessingError", error_msg)
            raise


    def create_errors_log_file_if_not_exists(self):
        if not os.path.exists(self.errors_log_file):
            with open(self.errors_log_file, "w") as log_file:
                log_file.write("Error Log File\n")

    def safe_parse_name(self, full_name: str) -> Tuple[str, str]:
        """Safely parse a full name into first and last names."""
        try:
            if pd.isna(full_name) or not isinstance(full_name, str):
                return ('Unknown', 'Unknown')

            parsed_name = HumanName(full_name)
            first = parsed_name.first or 'Unknown'
            last = parsed_name.last or 'Unknown'
            return (first, last)
        except Exception as e:
            self.log_error("NameParsingError", f"Error parsing name '{full_name}': {str(e)}")
            return ('Unknown', 'Unknown')

    def safe_generate_email(self, row: pd.Series) -> str:
        """Safely generate email address from row data."""
        try:
            if pd.isna(row['Company URL']) or pd.isna(row['Pattern']):
                return ''

            domain = self.extract_domain(row['Company URL'])
            if not domain:
                return ''

            pattern = str(row['Pattern'])
            first_name = str(row['First Name']).lower()
            last_name = str(row['Last Name']).lower()

            email_local = pattern.replace('{first}', first_name)\
                               .replace('{last}', last_name)\
                               .replace('{f}', first_name[0] if first_name else '')\
                               .replace('{l}', last_name[0] if last_name else '')

            return f"{email_local}@{domain}"

        except Exception as e:
            self.log_error("EmailGenerationError", f"Error generating email: {str(e)}")
            return ''
    
    def extract_domain(self, url: str) -> str:
        """Safely extract domain from URL."""
        try:
            if not url:
                return ''
            
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
                
            domain = urlparse(url).netloc
            return domain.replace('www.', '')
        except Exception as e:
            self.log_error("DomainExtractionError", f"Error extracting domain from {url}: {str(e)}")
            return ''
        
    def generate_emails(self, first_name, last_name, company_url, pattern):
        try:
            if not company_url.startswith('https://') and not company_url.startswith('http://'):
                company_url = 'https://' + company_url
            domain = urlparse(company_url).netloc
            domain = domain.replace('www.', '')

            placeholders = {
                '{first}': first_name.lower(),
                '{last}': last_name.lower(),
                '{f}': first_name[0].lower() if first_name else '',
                '{l}': last_name[0].lower() if last_name else '',
                '{first}.{last}': f"{first_name.lower()}.{last_name.lower()}",
                '{first}_{last}': f"{first_name.lower()}_{last_name.lower()}",
                '{f}_{last}': f"{first_name[0].lower()}_{last_name.lower()}" if first_name and last_name else ''
            }

            email = pattern
            for placeholder, value in placeholders.items():
                email = email.replace(placeholder, value)
            return f"{email}@{domain}"
        except Exception as e:
            error_msg = f"Error in generate_emails: {str(e)}\n{traceback.format_exc()}"
            self.log_error("EmailGenerationError", error_msg)
            raise

    def verify_email(self, email: str) -> bool:
        try:
            validate_email(email, check_deliverability=True, timeout=self.TIMEOUT)
            return True
        except Exception as e:
            self.log_error("EmailValidationError", f"Error verifying {email}: {str(e)}")
            return self.manual_validation(email)

    def manual_validation(self, email):
        try:
            return "@" in email
        except Exception as e:
            error_msg = f"Error in manual validation: {str(e)}\n{traceback.format_exc()}"
            self.log_error("ManualValidationError", error_msg)
            return False

    def log_error(self, error_type, error_message):
        try:
            with open(self.errors_log_file, "a") as log_file:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                log_file.write(f"\n[{timestamp}] {error_type}:\n{error_message}\n")
        except Exception as e:
            print(f"Error writing to log file: {str(e)}")

    def extract_phone_number(self, url: str) -> Optional[str]:
        if not url or url in self.visited_urls:
            return None

        self.visited_urls.add(url)
        
        try:
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }

            response = self.session.get(url, timeout=self.TIMEOUT, headers=headers)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            pattern = r'\+?(?:[-\s()]?\d[-\s()]?){8,20}'
            
            for tag in soup.find_all(["a", "p", "span", "div"], string=True):
                matches = re.findall(pattern, tag.get_text())
                if matches:
                    return matches[0].strip()

            return None

        except Exception as e:
            self.log_error("PhoneExtractionError", f"Error extracting phone from {url}: {str(e)}")
            return None
