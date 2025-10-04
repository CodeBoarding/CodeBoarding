import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class TypeScriptConfigScanner:
    """
    Scanner for TypeScript configuration files that can detect multiple TypeScript projects
    within a monorepo structure.
    """

    def __init__(self, repo_location: Path):
        self.repo_location = repo_location
        self.config_files = ['tsconfig.json', 'jsconfig.json']

    def scan(self) -> List[Dict]:
        """
        Scan the repository for TypeScript configuration files.
        
        Returns:
            List[Dict]: List of TypeScript project configurations with their paths
        """
        projects = []
        
        # Recursively find all TypeScript configuration files
        for config_file in self.config_files:
            config_paths = list(self.repo_location.rglob(config_file))
            
            for config_path in config_paths:
                project_dir = config_path.parent
                
                # Skip node_modules directories
                if 'node_modules' in str(project_dir):
                    continue
                
                # Parse the config file to validate it's a valid TypeScript project
                config_data = self._parse_config_file(config_path)
                if config_data:
                    project_info = {
                        'config_path': config_path,
                        'project_dir': project_dir,
                        'config_data': config_data,
                        'is_valid': self._validate_project(config_path, project_dir)
                    }
                    projects.append(project_info)
                    logger.info(f"Found TypeScript project at: {project_dir}")
        
        return projects

    def _parse_config_file(self, config_path: Path) -> Optional[Dict]:
        """Parse a TypeScript configuration file."""
        try:
            with open(config_path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            logger.warning(f"Failed to parse config file {config_path}: {e}")
            return None

    def _validate_project(self, config_path: Path, project_dir: Path) -> bool:
        """Validate that this is a legitimate TypeScript project."""
        # Check if there are actual TypeScript/JavaScript files in the project
        ts_extensions = ['.ts', '.tsx', '.js', '.jsx']
        
        for ext in ts_extensions:
            ts_files = list(project_dir.rglob(f'*{ext}'))
            # Filter out node_modules files
            ts_files = [f for f in ts_files if 'node_modules' not in str(f)]
            
            if ts_files:
                return True
        
        logger.debug(f"No TypeScript/JavaScript files found in project: {project_dir}")
        return False

    def get_project_directories(self) -> List[Path]:
        """
        Get all directories that contain valid TypeScript projects.
        
        Returns:
            List[Path]: List of project directory paths
        """
        projects = self.scan()
        return [project['project_dir'] for project in projects if project['is_valid']]

    def get_configurations(self) -> List[Dict]:
        """
        Get detailed information about all TypeScript configurations.
        
        Returns:
            List[Dict]: List of configuration information dictionaries
        """
        return self.scan()