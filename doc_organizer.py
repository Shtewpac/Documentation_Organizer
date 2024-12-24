import os
import json
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv
from typing import Dict, List, Optional

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
        current_section = None
        current_content = []
        
        for element in self.soup.body.children:
            if element.name in ['h1', 'h2', 'h3']:
                if current_section:
                    sections.append({
                        'title': current_section,
                        'content': ''.join(str(c) for c in current_content)
                    })
                current_section = element.get_text().strip()
                current_content = []
            elif current_section:
                current_content.append(str(element))
                
        # Add the last section
        if current_section:
            sections.append({
                'title': current_section,
                'content': ''.join(str(c) for c in current_content)
            })
            
        return sections

class GPTProcessor:
    """Handles interaction with GPT-4 for processing documentation sections."""
    
    def __init__(self):
        load_dotenv()
        self.client = OpenAI()
        
    def process_section(self, section: Dict[str, str]) -> Dict:
        """
        Process a documentation section using GPT-4 to generate structured output.
        """
        prompt = self._create_prompt(section)
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are an API documentation expert."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"Error processing section '{section['title']}': {str(e)}")
            return None
            
    def _create_prompt(self, section: Dict[str, str]) -> str:
        """Creates the prompt for GPT-4 with the section content."""
        return f"""
        Analyze the following HTML documentation section and provide a structured breakdown.
        Title: {section['title']}
        Content: {section['content']}
        
        Provide your response in the following JSON format:
        {{
            "section_type": "endpoint" | "concept" | "overview" | "other",
            "related_endpoints": ["list", "of", "related", "endpoints"],
            "filename": "suggested_filename.md",
            "content": "markdown content"
        }}
        
        Ensure the filename is URL-safe and ends with .md
        Convert the content to well-formatted Markdown
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
        for section in processed_sections:
            if not section:  # Skip failed sections
                continue
                
            # Determine the appropriate subdirectory
            subdir = section['section_type'] + 's'
            if section['section_type'] == 'other':
                subdir = 'overview'
                
            # Create the file
            file_path = os.path.join(self.output_dir, subdir, section['filename'])
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(section['content'])

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
        # Read and parse the HTML file
        with open(input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
            
        self.html_parser = HTMLParser(html_content)
        sections = self.html_parser.split_into_sections()
        
        # Process each section with GPT-4
        processed_sections = []
        for section in sections:
            processed = self.gpt_processor.process_section(section)
            if processed:
                processed_sections.append(processed)
                
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
    input_file = input("Enter the path to the HTML documentation file: ")
    output_dir = input("Enter the output directory path: ")
    
    # Create and run the organizer
    organizer = DocumentationOrganizer(output_dir)
    try:
        organizer.process_file(input_file)
        print(f"Documentation successfully organized in: {output_dir}")
    except Exception as e:
        print(f"Error processing documentation: {str(e)}")

if __name__ == "__main__":
    main()
