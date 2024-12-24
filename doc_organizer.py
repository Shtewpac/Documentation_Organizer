import os
import logging
import colorama
from colorama import Fore, Style
from typing import Dict, List, Optional
from pydantic import BaseModel
from dotenv import load_dotenv

# BeautifulSoup for HTML parsing
from bs4 import BeautifulSoup

# OpenAI client (assume you have a valid client that supports .beta.chat.completions.parse)
from openai import OpenAI

# Token counting (assume your environment supports tiktoken for your custom GPT models)
import tiktoken

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
CONFIG = {
    'gpt_model': 'gpt-4o-mini',  # e.g. "gpt-4o" or "gpt-4o-mini"
    'log_level': logging.DEBUG,
    'model_config': {
        'gpt-4o-mini': {
            'context_window': 128000,
            'max_output_tokens': 16384
        },
        'gpt-4o': {
            'context_window': 128000,
            'max_output_tokens': 16384
        }
    }
}


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
colorama.init()

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': Fore.BLUE,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        if record.levelname in self.COLORS:
            record.levelname = (
                f"{self.COLORS[record.levelname]}{record.levelname}{Style.RESET_ALL}"
            )
        return super().format(record)

# Ensure logs directory exists
logs_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(logs_dir, exist_ok=True)

logger = logging.getLogger()
logger.setLevel(CONFIG['log_level'])

# File handler with UTF-8 encoding
file_handler = logging.FileHandler(
    os.path.join(logs_dir, 'latest.log'),
    mode='w',  # Overwrite previous log
    encoding='utf-8'  # Specify UTF-8 encoding
)
file_handler.setFormatter(
    logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
)
logger.addHandler(file_handler)

# Console handler (colored) with UTF-8 encoding
if os.name == 'nt':  # Windows
    import sys
    # Force UTF-8 encoding for console output
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

console_handler = logging.StreamHandler()
console_handler.setFormatter(
    ColoredFormatter('%(levelname)s: %(message)s')
)
logger.addHandler(console_handler)

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class DocumentSection:
    """Represents a section of documentation with hierarchical structure."""

    def __init__(self, title: str, level: int):
        self.title = title
        self.level = level  # h1=1, h2=2, etc.
        self.content: List[str] = []
        self.subsections: List['DocumentSection'] = []
        self.parent: Optional['DocumentSection'] = None

    def add_content(self, content: str):
        """Add content to this section."""
        if content.strip():
            self.content.append(content.strip())

    def add_subsection(self, section: 'DocumentSection'):
        """Add a subsection and set its parent."""
        section.parent = self
        self.subsections.append(section)

    def get_full_content(self) -> str:
        """Get all content including subsections recursively."""
        lines = []
        # This section’s content
        if self.title:
            lines.append(f"## {self.title}\n")  # A top-level heading in Markdown could be H2, etc.
        lines.append("\n".join(self.content))

        # Subsections’ content
        for subsection in self.subsections:
            lines.append(subsection.get_full_content())

        return "\n".join(lines)

    def get_breadcrumbs(self) -> List[str]:
        """Get the full path of section titles from root to this section."""
        if self.parent is None:
            return [self.title]
        return self.parent.get_breadcrumbs() + [self.title]


class ProcessedSection(BaseModel):
    section_type: str
    related_endpoints: List[str]
    filename: str
    content: str


# ---------------------------------------------------------------------------
# HTMLParser
# ---------------------------------------------------------------------------
class HTMLParser:
    """Handles parsing and sectioning of HTML documentation by headings."""

    def __init__(self, html_content: str):
        self.soup = BeautifulSoup(html_content, 'html.parser')

    def _get_heading_level(self, tag_name: str) -> int:
        """Get numeric level from heading tag (h1=1, h2=2, etc.)."""
        # e.g., "h2" -> 2
        if tag_name.lower().startswith('h') and tag_name[1:].isdigit():
            return int(tag_name[1:])
        return 0

    def build_section_tree(self) -> DocumentSection:
        """
        Build a hierarchical tree of DocumentSection objects using headings.
        Returns a dummy root node (level 0), whose children are real top-level sections.
        """
        # We'll create a root with level=0 that isn't an actual doc section
        root = DocumentSection(title="ROOT", level=0)
        current_sections = {0: root}  # Map heading level -> DocumentSection

        # Choose the main container: often <main>, <article>, or body
        main_content = self.soup.find(['main', 'article']) or self.soup.body
        if not main_content:
            logger.warning("No main/article/body found, defaulting to full HTML.")
            main_content = self.soup

        for element in main_content.find_all():
            level = self._get_heading_level(element.name)

            if level > 0:
                # This is a heading. Create a new DocumentSection
                title_text = element.get_text().strip()
                new_section = DocumentSection(title=title_text, level=level)

                # Find the correct parent: the next heading up with a smaller level
                parent_level = level - 1
                while parent_level > 0 and parent_level not in current_sections:
                    parent_level -= 1
                while parent_level > 0 and current_sections[parent_level] is None:
                    parent_level -= 1

                # If we can find an existing parent, add subsection
                if parent_level >= 0 and current_sections.get(parent_level):
                    current_sections[parent_level].add_subsection(new_section)
                else:
                    # Otherwise, attach to the root
                    root.add_subsection(new_section)

                # Update current_sections for this level
                current_sections[level] = new_section

                # Clear deeper levels
                deeper = [lvl for lvl in current_sections if lvl > level]
                for dlevel in deeper:
                    current_sections[dlevel] = None

            else:
                # This is not a heading. Add content to the *deepest* active section
                # (the highest level that isn't None)
                deepest_section = None
                for l in sorted(current_sections.keys(), reverse=True):
                    if current_sections[l] is not None:
                        deepest_section = current_sections[l]
                        break
                if deepest_section is not None and str(element).strip():
                    # Special handling for code blocks
                    if element.name in ['code', 'pre']:
                        text = element.get_text()
                        content_str = f"```\n{text.strip()}\n```"
                    else:
                        # Keep minimal HTML or just plaintext
                        text = element.get_text("\n", strip=True)
                        content_str = text
                    deepest_section.add_content(content_str)

        return root

    def flatten_sections(self, root: DocumentSection) -> List[Dict[str, str]]:
        """
        Flatten the hierarchical DocumentSection tree into a list of dicts:
        [
          { "title": "Some Title", "content": "...", "breadcrumbs": [...] },
          ...
        ]
        """
        flat_list = []

        def traverse(section: DocumentSection):
            if section.title != "ROOT":
                # Build content from that node
                flat_list.append({
                    "title": section.title,
                    "content": section.get_full_content(),
                    "breadcrumbs": section.get_breadcrumbs()
                })
            for child in section.subsections:
                traverse(child)

        traverse(root)
        return flat_list

    def split_into_sections(self) -> List[Dict[str, str]]:
        """Public method to get a list of section dicts from the HTML."""
        root_section = self.build_section_tree()
        flat_sections = self.flatten_sections(root_section)
        logger.info(f"Found {len(flat_sections)} flattened sections from HTML.")
        return flat_sections


# ---------------------------------------------------------------------------
# GPTProcessor
# ---------------------------------------------------------------------------
class GPTProcessor:
    """Handles interaction with GPT for processing documentation sections."""

    def __init__(self):
        load_dotenv()
        self.client = OpenAI()  # We'll trust that you have an appropriate client
        self.model = CONFIG['gpt_model']
        self.model_config = CONFIG['model_config'][self.model]
        self.tokenizer = tiktoken.encoding_for_model(self.model)

        logger.info(f"Initialized GPTProcessor with model: {self.model}")

    def _count_tokens(self, text: str) -> int:
        """Count tokens in a text string using tiktoken."""
        return len(self.tokenizer.encode(text))

    def _call_gpt(self, prompt: str, title: str) -> Optional[Dict]:
        """Call GPT with the given prompt and return the parsed ProcessedSection dict."""
        try:
            logger.info(f"Processing section '{title}' with model {self.model}.")
            logger.debug(f"Prompt length: {len(prompt)} chars, {self._count_tokens(prompt)} tokens")
            
            completion = self.client.beta.chat.completions.parse(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an API documentation expert."},
                    {"role": "user", "content": prompt}
                ],
                response_format=ProcessedSection,
                max_tokens=self.model_config['max_output_tokens']
            )
            
            # Debug the raw response
            logger.debug(f"Raw completion response: {completion}")
            
            # Check for refusals or empty responses
            if not completion or not completion.choices:
                logger.error(f"Empty completion response for section '{title}'")
                return None
                
            message = completion.choices[0].message
            if hasattr(message, 'refusal'):
                logger.warning(
                    f"Model refused to process section '{title}': {message.refusal}"
                )
                return None
            
            # Debug the parsed result
            result = message.parsed.dict() if hasattr(message, 'parsed') else None
            if result:
                logger.debug(f"Parsed result: {result}")
            else:
                logger.error(f"Failed to parse completion response for section '{title}'")
            
            return result

        except Exception as e:
            logger.error(
                f"Error calling GPT for section '{title}': {str(e)}",
                exc_info=True
            )
            return None

    def process_section(self, section: Dict[str, str]) -> Optional[Dict]:
        """Process a single doc section with GPT, return a structured dict or None."""
        try:
            # Debug input section
            logger.debug(f"Processing section with title: {section['title']}")
            logger.debug(f"Section content length: {len(section['content'])} chars")
            logger.debug(f"Section breadcrumbs: {section.get('breadcrumbs', [])}")
            
            prompt = self._create_prompt(section)
            token_count = self._count_tokens(prompt)
            
            # Debug token counts
            logger.debug(f"Token count for section '{section['title']}': {token_count}")
            logger.debug(f"Context window size: {self.model_config['context_window']}")
            
            if token_count > self.model_config['context_window']:
                logger.warning(
                    f"Section '{section['title']}' exceeds token limit "
                    f"({token_count} > {self.model_config['context_window']}). Splitting..."
                )
                return self._process_large_section(section)
            else:
                result = self._call_gpt(prompt, section["title"])
                if result:
                    logger.debug(f"Successfully processed section '{section['title']}'")
                    logger.debug(f"Result type: {result['section_type']}")
                    logger.debug(f"Generated filename: {result['filename']}")
                return result
                
        except Exception as e:
            logger.error(
                f"Error processing section '{section['title']}': {str(e)}",
                exc_info=True
            )
            return None

    def _process_large_section(self, section: Dict[str, str]) -> Optional[Dict]:
        """
        Split a large section by paragraphs (rather than lines) and
        re-combine results at the end.
        """
        content = section["content"]
        paragraphs = content.split("\n\n")  # A naive paragraph split

        # Accumulate small chunks
        chunked_paragraphs = []
        current_chunk = []
        current_tokens = 0

        for para in paragraphs:
            p_tokens = self._count_tokens(para)
            if current_tokens + p_tokens > (self.model_config['context_window'] // 2):
                # Start a new chunk
                chunked_paragraphs.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_tokens = p_tokens
            else:
                current_chunk.append(para)
                current_tokens += p_tokens

        if current_chunk:
            chunked_paragraphs.append("\n\n".join(current_chunk))

        # Now process each chunk individually
        processed_chunks = []
        for i, chunk_text in enumerate(chunked_paragraphs):
            sub_section = {
                "title": f"{section['title']} (Part {i+1})",
                "content": chunk_text,
                "breadcrumbs": section.get("breadcrumbs", []) + [f"Part {i+1}"]
            }
            logger.info(f"Processing chunk {i+1} of section '{section['title']}'")
            part_result = self.process_section(sub_section)
            if part_result:
                processed_chunks.append(part_result)

        # If nothing worked, bail
        if not processed_chunks:
            return None

        # Combine partial results
        combined = processed_chunks[0]
        for part in processed_chunks[1:]:
            combined['content'] += f"\n\n{part['content']}"
            combined['related_endpoints'].extend(part['related_endpoints'])

        return combined

    def _create_prompt(self, section: Dict[str, str]) -> str:
        """Prompt template for GPT model."""
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
  in the content. Return an empty list if none found.

- filename: Create a URL-safe filename ending in .md.
  Convert spaces to hyphens, remove special characters, use lowercase.

- content: The section content converted to well-formatted Markdown.
  Ensure proper heading hierarchy and code block formatting.
"""


# ---------------------------------------------------------------------------
# FileGenerator
# ---------------------------------------------------------------------------
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

    def generate_files(self, processed_sections: List[Dict]):
        """Generates files from processed documentation sections."""
        logger.info(f"Generating files for {len(processed_sections)} processed sections")

        for section in processed_sections:
            if not section:
                logger.warning("Skipping None section")
                continue

            try:
                # Determine the appropriate subdirectory
                s_type = section['section_type']
                subdir = 'endpoints'
                if s_type == 'concept':
                    subdir = 'concepts'
                elif s_type in ('overview', 'other'):
                    subdir = 'overview'

                file_path = os.path.join(
                    self.output_dir, subdir, section['filename']
                )
                logger.info(f"Writing file: {file_path}")

                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(section['content'])

                logger.info(f"Successfully wrote file: {file_path}")
            except Exception as e:
                logger.error(
                    f"Error writing file for section: {e}",
                    exc_info=True
                )


# ---------------------------------------------------------------------------
# DocumentationOrganizer
# ---------------------------------------------------------------------------
class DocumentationOrganizer:
    """Orchestrates the entire documentation organization process."""

    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.gpt_processor = GPTProcessor()
        self.file_generator = FileGenerator(output_dir)

    def process_file(self, input_file: str) -> None:
        """Process a single HTML documentation file."""
        logger.info(f"Processing file: {input_file}")

        # Load HTML
        with open(input_file, 'r', encoding='utf-8') as f:
            html_content = f.read()

        # Parse sections
        parser = HTMLParser(html_content)
        sections = parser.split_into_sections()
        logger.info(f"Flattened to {len(sections)} total sections")

        # GPT-process each
        processed_sections = []
        for sec in sections:
            processed = self.gpt_processor.process_section(sec)
            if processed:
                processed_sections.append(processed)

        logger.info(f"Successfully processed {len(processed_sections)} sections")

        # Generate output files
        self.file_generator.generate_files(processed_sections)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    """Main entry point for the documentation organizer."""
    load_dotenv()

    # Ensure OpenAI API key is set
    if not os.getenv('OPENAI_API_KEY'):
        print("Error: OPENAI_API_KEY environment variable is not set")
        return

    # Example usage
    input_file = r"C:\Users\WilliamKraft\Documents\Coding Projects\Documentation_Organizer\example_documentation\alpha_vantage\Alpha X Data Governance.html"
    output_dir = r"C:\Users\WilliamKraft\Documents\Coding Projects\Documentation_Organizer\example_documentation\alpha_vantage\organized"

    organizer = DocumentationOrganizer(output_dir)
    try:
        organizer.process_file(input_file)
        print(f"Documentation successfully organized in: {output_dir}")
    except Exception as e:
        print(f"Error processing documentation: {str(e)}")


if __name__ == "__main__":
    main()
