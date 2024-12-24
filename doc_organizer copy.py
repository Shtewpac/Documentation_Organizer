import os
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv
from typing import Dict, List, Optional
from pydantic import BaseModel
import logging
import colorama
from colorama import Fore, Style

# Configuration
CONFIG = {
    'gpt_model': 'gpt-4o-mini',  # Options: gpt-4o-mini, gpt-4o-2024-08-06
    'log_level': logging.DEBUG
}

# Initialize colorama for Windows support
colorama.init()

# Custom formatter for colored console output
class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': Fore.BLUE,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        # Add color to the level name
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{Style.RESET_ALL}"
        return super().format(record)

# Create logs directory
logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(logs_dir, exist_ok=True)

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

# Create and configure file handler
file_handler = logging.FileHandler(
    os.path.join(os.path.dirname(__file__), 'logs', 'latest.log'),
    mode='w'  # Overwrite previous log
)
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)
logger.addHandler(file_handler)

# Create and configure colored console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(
    ColoredFormatter('%(levelname)s: %(message)s')
)
logger.addHandler(console_handler)

class HTMLParser:
    """Handles parsing and sectioning of HTML documentation."""
    
    def __init__(self, html_content: str):
        self.soup = BeautifulSoup(html_content, 'html.parser')
        
    def split_into_sections(self) -> List[Dict[str, str]]:
        """
        Splits HTML content into logical sections based on heading tags.
        Returns a list of dictionaries containing section title and content.
        """
        sections = []
        # Find all heading tags in the document
        headings = self.soup.find_all(['h1', 'h2', 'h3', 'h4'])
        
        for i, heading in enumerate(headings):
            title = heading.get_text().strip()
            content = []
            
            # Get all elements between this heading and the next one
            current = heading.next_sibling
            while current and current not in headings:
                if str(current).strip():  # Only add non-empty elements
                    content.append(str(current))
                current = current.next_sibling if hasattr(current, 'next_sibling') else None
            
            if title and content:  # Only add sections with both title and content
                sections.append({
                    'title': title,
                    'content': '\n'.join(content)
                })
                logger.debug(f"Found section: {title}")
                
        logger.info(f"Found {len(sections)} sections in HTML")
        return sections

class ProcessedSection(BaseModel):
    section_type: str
    related_endpoints: list[str]
    filename: str
    content: str

class GPTProcessor:
    """Handles interaction with GPT for processing documentation sections."""
    
    def __init__(self):
        load_dotenv()
        self.client = OpenAI()
        self.model = CONFIG['gpt_model']
        logger.info(f"Initialized GPTProcessor with model: {self.model}")
        
    def process_section(self, section: Dict[str, str]) -> Optional[Dict]:
        """
        Process a documentation section using specified GPT model to generate structured output.
        """
        try:
            logger.info(f"Processing section: {section['title']} with model {self.model}")
            
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an API documentation expert."},
                    {"role": "user", "content": self._create_prompt(section)}
                ],
                response_format=ProcessedSection
            )
            
            # Check for refusals
            if hasattr(completion.choices[0].message, 'refusal'):
                logger.warning(f"Model refused to process section '{section['title']}': {completion.choices[0].message.refusal}")
                return None
            
            result = completion.choices[0].message.parsed.dict()
            logger.info(f"Successfully processed section: {section['title']} -> {result['filename']}")
            return result
            
        except Exception as e:
            logger.error(f"Error processing section '{section['title']}': {str(e)}", exc_info=True)
            return None
            
    def _create_prompt(self, section: Dict[str, str]) -> str:
        """Creates the prompt for GPT-4 with the section content."""
        return f"""
        Analyze the following HTML documentation section and provide a structured breakdown.
        Title: {section['title']}
        Content: {section['content']}
        
        Format your response to match these exact field requirements:

        - section_type: Must be one of ["endpoint", "concept", "overview", "other"]
          Choose based on the content type:
          - "endpoint" for API endpoint documentation
          - "concept" for explanatory content about concepts
          - "overview" for introductory or high-level content
          - "other" for anything else

        - related_endpoints: A list of strings containing any API endpoints mentioned 
          in the content. Return empty list if none found.

        - filename: Create a URL-safe filename ending in .md
          Convert spaces to hyphens, remove special characters, use lowercase

        - content: The section content converted to well-formatted Markdown.
          Ensure proper heading hierarchy and code block formatting.
        """

class FileGenerator:
    """Handles generation of output files from processed documentation."""
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self._create_directory_structure()
        
    def _create_directory_structure(self):
        """Creates the necessary directory structure for output files."""
        for dir_name in ['endpoints', 'concepts', 'overview']:
            dir_path = os.path.join(self.output_dir, dir_name)
            os.makedirs(dir_path, exist_ok=True)
            
    def generate_files(self, processed_sections: List[Dict]) -> None:
        """
        Generates files from processed documentation sections.
        """
        logger.info(f"Generating files for {len(processed_sections)} processed sections")
        
        for section in processed_sections:
            if not section:
                logger.warning("Skipping None section")
                continue
            
            try:
                # Determine the appropriate subdirectory
                subdir = section['section_type'] + 's'
                if section['section_type'] == 'other':
                    subdir = 'overview'
                
                # Create the file
                file_path = os.path.join(self.output_dir, subdir, section['filename'])
                logger.info(f"Writing file: {file_path}")
                
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(section['content'])
                    
                logger.info(f"Successfully wrote file: {file_path}")
                
            except Exception as e:
                logger.error(f"Error writing file for section: {e}", exc_info=True)

class DocumentationOrganizer:
    """Main class that orchestrates the documentation organization process."""
    
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.html_parser = None
        self.gpt_processor = GPTProcessor()
        self.file_generator = FileGenerator(output_dir)
        
    def process_file(self, input_file: str) -> None:
        """
        Process a single HTML documentation file.
        """
        logger.info(f"Processing file: {input_file}")
        
        # Read and parse the HTML file
        with open(input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
            
        self.html_parser = HTMLParser(html_content)
        sections = self.html_parser.split_into_sections()
        logger.info(f"Found {len(sections)} sections in file")
        
        # Process each section with GPT-4
        processed_sections = []
        for section in sections:
            processed = self.gpt_processor.process_section(section)
            if processed:
                processed_sections.append(processed)
                
        logger.info(f"Successfully processed {len(processed_sections)} sections")
        
        # Generate output files
        self.file_generator.generate_files(processed_sections)

def main():
    """Main entry point for the documentation organizer."""
    load_dotenv()
    
    # Ensure OpenAI API key is set
    if not os.getenv('OPENAI_API_KEY'):
        print("Error: OPENAI_API_KEY environment variable is not set")
        return
        
    # Get input and output paths
    # input_file = input("Enter the path to the HTML documentation file: ")
    # output_dir = input("Enter the output directory path: ")
    input_file = r"C:\Users\WilliamKraft\Documents\Coding Projects\Documentation_Organizer\example_documentation\alpha_vantage\Alpha X Data Governance.html"
    output_dir = r"C:\Users\WilliamKraft\Documents\Coding Projects\Documentation_Organizer\example_documentation\alpha_vantage\organized"
    
    # Create and run the organizer
    organizer = DocumentationOrganizer(output_dir)
    try:
        organizer.process_file(input_file)
        print(f"Documentation successfully organized in: {output_dir}")
    except Exception as e:
        print(f"Error processing documentation: {str(e)}")

if __name__ == "__main__":
    main()
