"""
Configuration management for Azure DevOps MCP Monitor
"""
import os
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config(BaseSettings):
    """Application configuration"""
    
    # Azure DevOps Configuration
    azure_devops_org: str = Field(..., env="AZURE_DEVOPS_ORG")
    azure_devops_project: str = Field(..., env="AZURE_DEVOPS_PROJECT")
    azure_devops_pat: str = Field(..., env="AZURE_DEVOPS_PAT")
    
    # GitHub Configuration
    github_token: str = Field(..., env="GITHUB_TOKEN")
    github_repo: str = Field(..., env="GITHUB_REPO")
    github_workflow_file: str = Field(..., env="GITHUB_WORKFLOW_FILE")
    github_default_branch: str = Field(default="main", env="GITHUB_DEFAULT_BRANCH")
    
    # Monitoring Configuration
    target_column: str = Field(default="Testing", env="TARGET_COLUMN")
    polling_interval: int = Field(default=30, env="POLLING_INTERVAL")
    max_retries: int = Field(default=3, env="MAX_RETRIES")
    
    # Logging Configuration
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = Field(default="json", env="LOG_FORMAT")
    
    # MCP Server Configuration
    mcp_server_host: str = Field(default="localhost", env="MCP_SERVER_HOST")
    mcp_server_port: int = Field(default=8080, env="MCP_SERVER_PORT")
    
    # Optional Webhook Configuration
    webhook_secret: Optional[str] = Field(default=None, env="WEBHOOK_SECRET")
    webhook_port: int = Field(default=8081, env="WEBHOOK_PORT")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_config() -> Config:
    """Get application configuration"""
    return Config()


# Global configuration instance
config = get_config()
