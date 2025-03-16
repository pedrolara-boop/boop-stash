#!/usr/bin/env python3
"""
StashStudioMetadataMatcherPlugin

A plugin for matching studios in Stashapp database with ThePornDB and StashDB.

GitHub: https://github.com/pedrolara-boop/StashStudioMetadataMatcher
License: MIT
"""

import json
import sys
import os
import importlib.util
import requests
from datetime import datetime
from stashapi.stashapp import StashInterface
import stashapi.log as log
import logging
from logging.handlers import RotatingFileHandler
from thefuzz import fuzz

# Import core functionality from the main script
from StashStudioMetadataMatcher import (
    logger, update_all_studios, update_single_studio, find_studio_by_name,
    graphql_request, find_local_studio, get_all_studios,
    search_studio, search_tpdb_site, find_studio, find_tpdb_site,
    find_or_create_parent_studio, add_tpdb_id_to_studio, update_studio,
    update_studio_data
)

# Constants for API endpoints
TPDB_API_URL = "https://theporndb.net/graphql"
TPDB_REST_API_URL = "https://api.theporndb.net"
STASHDB_API_URL = "https://stashdb.org/graphql"

# GraphQL queries for standard Stash-box endpoints
STASHBOX_SEARCH_STUDIO_QUERY = """
query SearchStudio($term: String!) {
    searchStudio(term: $term) {
        id
        name
    }
}
"""

STASHBOX_FIND_STUDIO_QUERY = """
query FindStudio($id: ID!) {
    findStudio(id: $id) {
        id
        name
        urls {
            url
            type
        }
        parent {
            id
            name
        }
        images {
            url
        }
    }
}
"""

config = {}  # Initialize empty config dictionary

def str_to_bool(value):
    """Convert string or boolean value to boolean"""
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'on')

def setup_rotating_logger(log_path):
    """Set up a rotating logger that will create new files when the size limit is reached"""
    # Create a rotating file handler
    max_bytes = 10 * 1024 * 1024  # 10MB per file
    backup_count = 5  # Keep 5 backup files
    
    # Ensure the directory exists
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    
    # Create the rotating handler
    rotating_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding='utf-8'
    )
    
    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    rotating_handler.setFormatter(formatter)
    
    # Create logger
    logger = logging.getLogger('StashStudioMetadataMatcher')
    logger.setLevel(logging.INFO)
    logger.addHandler(rotating_handler)
    
    return logger

def main():
    """
    Main function for the plugin version.
    Reads plugin arguments from stdin and processes studios accordingly.
    """
    global config  # Reference the global config
    try:
        if not sys.stdin.isatty():
            plugin_input = json.loads(sys.stdin.read())
            server_connection = plugin_input.get('server_connection', {})
            plugin_args = plugin_input.get('args', {})
            
            # Create a StashInterface using the server connection details
            stash = StashInterface(server_connection)
            stash_config = stash.get_configuration()
            
            # Create our config dictionary
            config.update({
                'scheme': server_connection.get('Scheme', 'http'),
                'host': server_connection.get('Host', 'localhost'),
                'port': server_connection.get('Port', 9999),
                'api_key': server_connection.get('ApiKey', ''),
                'tpdb_api_key': '',
                'stashdb_api_key': '',
                'log_file': 'studio_metadata_matcher.log',
                'fuzzy_threshold': 90,
                'use_fuzzy_matching': True,
                'stash_interface': stash,
                'stashbox_endpoints': []
            })
            
            # Get API keys from Stash configuration
            if 'stashBoxes' in stash_config.get('general', {}):
                logger("🔍 Configuring Stash-box endpoints:", "INFO")
                configured_endpoints = set()  # Track unique endpoints
                
                for stash_box in stash_config['general']['stashBoxes']:
                    endpoint = stash_box.get('endpoint', '')
                    api_key = stash_box.get('api_key', '')
                    name = stash_box.get('name', 'Unknown')
                    
                    if endpoint and api_key:
                        # Skip duplicate endpoints
                        if endpoint in configured_endpoints:
                            logger(f"⚠️ Skipping duplicate endpoint: {name} ({endpoint})", "INFO")
                            continue
                            
                        configured_endpoints.add(endpoint)
                        is_tpdb = "theporndb.net" in endpoint.lower()
                        
                        endpoint_info = {
                            'name': name,
                            'endpoint': endpoint,
                            'api_key': api_key,
                            'is_tpdb': is_tpdb
                        }
                        
                        config['stashbox_endpoints'].append(endpoint_info)
                        
                        if is_tpdb:
                            config['tpdb_api_key'] = api_key
                            logger(f"✅ Added ThePornDB endpoint: {name}", "INFO")
                        else:
                            # All other endpoints are treated as standard Stash-boxes
                            logger(f"✅ Added Stash-box endpoint: {name} ({endpoint})", "INFO")
                
                # Summary of configured endpoints
                stashbox_count = len([e for e in config['stashbox_endpoints'] if not e['is_tpdb']])
                has_tpdb = any(e['is_tpdb'] for e in config['stashbox_endpoints'])
                logger(f"📊 Total endpoints configured: {len(config['stashbox_endpoints'])} ({stashbox_count} Stash-boxes, TPDB: {has_tpdb})", "INFO")
            
            # Get plugin arguments
            dry_run = str_to_bool(plugin_args.get('dry_run', False))
            force = str_to_bool(plugin_args.get('force', False))
            studio_id = plugin_args.get('studio_id')
            
            # Make the mode setting visible in the logs at startup
            mode_str = " (FORCE)" if force else " (DRY RUN)" if dry_run else ""
            log.info(f"🚀 Starting StashStudioMetadataMatcherPlugin{mode_str} - Fuzzy threshold: {config['fuzzy_threshold']}")
            
            # Process single studio or all studios
            if studio_id:
                log.info(f"🔍 Running update for single studio ID: {studio_id}")
                studio = find_local_studio(studio_id)
                if studio:
                    wrapped_update_studio_data(studio, dry_run, force)
                else:
                    log.error(f"❌ Studio with ID {studio_id} not found.")
            else:
                log.info("🔄 Running update for all studios")
                update_all_studios(dry_run, force)
            
            log.info("✅ StashStudioMetadataMatcherPlugin completed")
        else:
            print("No input received from stdin. This script is meant to be run as a Stash plugin.")
    except json.JSONDecodeError:
        print("Failed to decode JSON input. This script is meant to be run as a Stash plugin.")
    except Exception as e:
        print(f"Error in StashStudioMetadataMatcherPlugin: {str(e)}")

def search_tpdb_site(term, api_key):
    """Search for a site on ThePornDB using the REST API"""
    logger(f"Searching for site '{term}' on ThePornDB REST API", "DEBUG")
    
    if not api_key:
        logger("No ThePornDB API key provided, skipping search", "DEBUG")
        return []
    
    url = f"{TPDB_REST_API_URL}/sites"
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json'
    }
    params = {
        'q': term,
        'limit': 100,  # Get more results to improve matching chances
        'sort': 'name',  # Sort by name for better matching
        'status': 'active',  # Only get active sites
        'include': 'parent,network',  # Include parent and network data in response
        'order': 'desc',  # Most relevant first
        'date_updated': 'last_month'  # Prioritize recently updated sites
    }
    
    try:
        # Add timeout to prevent hanging
        logger(f"Making request to {url} with query: {term}", "DEBUG")
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        # Log the actual URL being called (for debugging)
        logger(f"Full URL with params: {response.url}", "DEBUG")
        
        response.raise_for_status()
        data = response.json()
        
        if 'data' in data:
            sites = data['data']
            logger(f"Found {len(sites)} results for '{term}' on ThePornDB REST API", "DEBUG")
            
            # Convert to the same format as our GraphQL results
            results = []
            for site in sites:
                # Only include if we have a valid UUID
                if site.get('uuid'):
                    # Include parent and network info if available
                    parent_info = None
                    if site.get('parent') and site['parent'].get('uuid'):
                        parent_info = {
                            'id': str(site['parent']['uuid']),
                            'name': site['parent'].get('name')
                        }
                    elif site.get('network') and site['network'].get('uuid'):
                        parent_info = {
                            'id': str(site['network']['uuid']),
                            'name': site['network'].get('name')
                        }
                    
                    results.append({
                        'id': str(site.get('uuid')),
                        'name': site.get('name'),
                        'parent': parent_info,
                        'date_updated': site.get('updated_at')
                    })
            return results
        else:
            logger(f"No 'data' field in ThePornDB response: {data}", "DEBUG")
            return []
    except requests.exceptions.RequestException as e:
        logger(f"ThePornDB REST API request failed: {e}", "ERROR")
        # Log more details about the error
        if hasattr(e, 'response') and e.response is not None and hasattr(e.response, 'text'):
            logger(f"Error response: {e.response.text}", "DEBUG")
        return []
    except Exception as e:
        logger(f"Unexpected error in search_tpdb_site: {e}", "ERROR")
        return []

def find_tpdb_site(site_id, api_key):
    """Find a site on ThePornDB using the REST API"""
    logger(f"Finding site with ID {site_id} on ThePornDB REST API", "DEBUG")
    
    url = f"{TPDB_REST_API_URL}/sites/{site_id}"
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json'
    }
    params = {
        'include': 'parent,network'  # Include parent and network data in response
    }
    
    try:
        # Add timeout to prevent hanging
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        response_data = response.json()
        
        # Log the raw response for debugging
        logger(f"Raw ThePornDB response: {response_data}", "DEBUG")
        
        # The API returns data wrapped in a 'data' object
        if 'data' in response_data:
            site = response_data['data']
            logger(f"Retrieved raw site data from ThePornDB REST API: {site}", "DEBUG")
            
            # Convert to the same format as our GraphQL results
            parent = None
            # Check for parent or network info in the included data
            if site.get('parent') and site['parent'].get('uuid'):
                parent = {
                    'id': str(site['parent']['uuid']),
                    'name': site['parent'].get('name')
                }
            elif site.get('network') and site['network'].get('uuid'):
                parent = {
                    'id': str(site['network']['uuid']),
                    'name': site['network'].get('name')
                }
            
            # Build the result in the same format as StashDB
            result = {
                'id': str(site.get('uuid')),
                'name': site.get('name'),
                'urls': [],
                'parent': parent,
                'images': [],
                'date_updated': site.get('updated_at')
            }
            
            # Add URL if available
            if site.get('url'):
                result['urls'].append({
                    'url': site.get('url'),
                    'type': 'HOME'
                })
            
            # Add images in priority order
            for image_field in ['poster', 'logo', 'image', 'background']:
                if site.get(image_field):
                    result['images'].append({
                        'url': site.get(image_field)
                    })
            
            logger(f"Processed site data from ThePornDB REST API: {result}", "DEBUG")
            return result
        
        logger(f"No data found in ThePornDB REST API response for site ID {site_id}", "ERROR")
        return None
    except requests.exceptions.RequestException as e:
        logger(f"ThePornDB REST API request failed: {e}", "ERROR")
        if hasattr(e, 'response') and e.response is not None and hasattr(e.response, 'text'):
            logger(f"Error response: {e.response.text}", "DEBUG")
        return None
    except Exception as e:
        logger(f"Unexpected error in find_tpdb_site: {e}", "ERROR")
        return None

def fuzzy_match_studio_name(name, candidates, threshold=85):
    """Enhanced fuzzy matching with clear result logging and endpoint tracking"""
    if not name or not candidates:
        logger("No name or candidates provided for fuzzy matching", "DEBUG")
        return None, 0, []
    
    # Group matches by endpoint for clearer logging
    matches_by_endpoint = {}
    best_matches = []  # Store best matches from each endpoint
    overall_best_match = None
    overall_best_score = 0
    
    for candidate in candidates:
        endpoint_name = candidate.get('endpoint_name', 'Unknown')
        score = fuzz.token_sort_ratio(name.lower(), candidate['name'].lower())
        
        if endpoint_name not in matches_by_endpoint:
            matches_by_endpoint[endpoint_name] = []
        
        matches_by_endpoint[endpoint_name].append({
            'name': candidate['name'],
            'score': score,
            'id': candidate['id'],
            'original': candidate
        })
        
        # Track best match per endpoint and overall
        if score >= threshold:
            if not matches_by_endpoint.get(f"{endpoint_name}_best_score") or score > matches_by_endpoint[f"{endpoint_name}_best_score"]:
                matches_by_endpoint[f"{endpoint_name}_best_score"] = score
                matches_by_endpoint[f"{endpoint_name}_best_match"] = candidate
                
            if score > overall_best_score:
                overall_best_score = score
                overall_best_match = candidate
    
    # Log results by endpoint
    logger(f"🎯 Fuzzy matching results for '{name}':", "INFO")
    for endpoint, matches in matches_by_endpoint.items():
        if isinstance(matches, list):  # Skip our _best_score and _best_match entries
            # Sort matches by score
            sorted_matches = sorted(matches, key=lambda x: x['score'], reverse=True)
            if sorted_matches:
                logger(f"   {endpoint}:", "INFO")
                # Show top 3 matches for each endpoint
                for match in sorted_matches[:3]:
                    match_type = "EXACT" if match['score'] == 100 else "FUZZY"
                    logger(f"      - {match['name']} ({match_type} Score: {match['score']}%)", "INFO")
                
                # If this endpoint had a match above threshold, add it to best matches
                best_for_endpoint = matches_by_endpoint.get(f"{endpoint}_best_match")
                if best_for_endpoint:
                    best_matches.append(best_for_endpoint)
    
    if overall_best_match is not None and overall_best_score >= threshold:
        logger(f"✅ Best overall match: '{overall_best_match['name']}' from {overall_best_match['endpoint_name']} (Score: {overall_best_score}%)", "INFO")
        # Return both the overall best match and all matches above threshold
        return overall_best_match, overall_best_score, best_matches
    else:
        logger(f"❌ No matches above threshold ({threshold}%)", "INFO")
        return None, 0, []

def search_all_stashboxes(studio_name):
    """Search for a studio across all configured Stash-box endpoints"""
    global config
    results = []
    
    logger(f"🔍 Searching for studio: {studio_name!r}", "INFO")
    
    # Fix nested f-string issue by pre-formatting the endpoint list
    endpoint_list = [f"{e['name']} ({e['endpoint']})" for e in config['stashbox_endpoints']]
    logger(f"🔧 Configured endpoints: {endpoint_list}", "INFO")
    
    for endpoint in config['stashbox_endpoints']:
        try:
            if not endpoint['api_key']:
                logger(f"⚠️ Skipping {endpoint['name']} - No API key", "INFO")
                continue
                
            logger(f"📌 Searching {endpoint['name']} ({endpoint['endpoint']})", "INFO")
            
            if endpoint['is_tpdb']:
                # TPDB REST API search
                tpdb_results = search_tpdb_site(studio_name, endpoint['api_key'])
                if tpdb_results:
                    for result in tpdb_results:
                        results.append({
                            'id': result['id'],
                            'name': result['name'],
                            'endpoint': endpoint['endpoint'],
                            'endpoint_name': endpoint['name'],
                            'api_key': endpoint['api_key'],
                            'is_tpdb': True,
                            'parent': result.get('parent')
                        })
            else:
                # Standard Stash-box search (StashDB and PMV)
                try:
                    logger(f"Making GraphQL request to {endpoint['endpoint']} for {studio_name!r}", "INFO")
                    response = graphql_request(
                        STASHBOX_SEARCH_STUDIO_QUERY, 
                        {'term': studio_name}, 
                        endpoint['endpoint'], 
                        endpoint['api_key']
                    )
                    
                    logger(f"GraphQL response from {endpoint['name']}: {response}", "DEBUG")
                    
                    if response and 'searchStudio' in response:
                        found_results = response['searchStudio']
                        if found_results:
                            for result in found_results:
                                results.append({
                                    'id': result['id'],
                                    'name': result['name'],
                                    'endpoint': endpoint['endpoint'],
                                    'endpoint_name': endpoint['name'],
                                    'api_key': endpoint['api_key'],
                                    'is_tpdb': False,
                                    'parent': result.get('parent')
                                })
                except Exception as e:
                    logger(f"GraphQL error for {endpoint['name']}: {str(e)}", "ERROR")
            
            # Log results for this endpoint
            endpoint_results = [r for r in results if r['endpoint'] == endpoint['endpoint']]
            if endpoint_results:
                logger(f"✨ Found {len(endpoint_results)} results on {endpoint['name']}", "INFO")
                for result in endpoint_results:
                    logger(f"   - {result['name']} (ID: {result['id']})", "INFO")
            else:
                logger(f"❌ No results found on {endpoint['name']}", "INFO")
                
        except Exception as e:
            logger(f"Error searching {endpoint['name']}: {e}", "ERROR")
            continue
    
    # After gathering all results, perform fuzzy matching
    if results:
        logger(f"🎯 Running fuzzy matching on {len(results)} total results", "INFO")
        best_match, score, all_matches = fuzzy_match_studio_name(studio_name, results)
        
        # Log matches by endpoint
        if all_matches:
            logger("🎯 Matches found from different endpoints:", "INFO")
            matches_by_endpoint = {}
            for match in all_matches:
                endpoint = match['endpoint_name']
                if endpoint not in matches_by_endpoint:
                    matches_by_endpoint[endpoint] = []
                matches_by_endpoint[endpoint].append(match)
            
            for endpoint, matches in matches_by_endpoint.items():
                logger(f"   {endpoint}:", "INFO")
                for match in matches:
                    logger(f"      - {match['name']} (ID: {match['id']})", "INFO")
        
        if best_match:
            logger(f"✅ Best overall match: {best_match['name']} from {best_match['endpoint_name']} (Score: {score}%)", "INFO")
            
        return all_matches if all_matches else []
    else:
        logger(f"❌ No matches found across any endpoints for {studio_name!r}", "INFO")
        return []

def wrapped_update_studio_data(studio, dry_run=False, force=False):
    """Update studio data with matches from all configured endpoints"""
    global config
    
    logger(f"🔄 Processing studio: {studio['name']}", "INFO")
    
    # Skip if studio already has all IDs and data (unless force is True)
    if not force and studio.get('stash_ids') and studio.get('image_path') and studio.get('url'):
        logger(f"✅ Studio {studio['name']} already has all data, skipping", "INFO")
        return
    
    # Get existing stash IDs
    existing_stash_ids = studio.get('stash_ids', [])
    existing_endpoints = {stash_id['endpoint']: stash_id['stash_id'] for stash_id in existing_stash_ids}
    
    # Search for matches across all endpoints
    matches = search_all_stashboxes(studio['name'])
    
    if not matches:
        logger(f"❌ No matches found for studio: {studio['name']}", "INFO")
        return
    
    # Initialize new stash IDs list with existing IDs
    new_stash_ids = existing_stash_ids.copy()
    
    # Track which endpoints we've processed
    processed_endpoints = set()
    
    # Process each match
    for match in matches:
        endpoint = match['endpoint']
        endpoint_name = match['endpoint_name']
        
        # Skip if we already have an ID for this endpoint
        if endpoint in existing_endpoints and not force:
            logger(f"ℹ️ Already have ID for {endpoint_name}, skipping", "INFO")
            continue
        
        # Skip if we've already processed this endpoint
        if endpoint in processed_endpoints:
            continue
        
        processed_endpoints.add(endpoint)
        
        try:
            if match['is_tpdb']:
                # Get full studio data from TPDB
                studio_data = find_tpdb_site(match['id'], match['api_key'])
            else:
                # Get full studio data from Stash-box
                response = graphql_request(
                    STASHBOX_FIND_STUDIO_QUERY,
                    {'id': match['id']},
                    endpoint,
                    match['api_key']
                )
                studio_data = response.get('findStudio') if response else None
            
            if studio_data:
                # Add or update stash ID for this endpoint
                stash_id = {
                    'endpoint': endpoint,
                    'stash_id': studio_data['id']
                }
                
                # Remove existing ID for this endpoint if it exists
                new_stash_ids = [sid for sid in new_stash_ids if sid['endpoint'] != endpoint]
                new_stash_ids.append(stash_id)
                
                logger(f"✅ Added/Updated ID for {endpoint_name}: {studio_data['id']}", "INFO")
                
                # Process parent studio if present
                if studio_data.get('parent'):
                    parent_data = studio_data['parent']
                    logger(f"📦 Found parent studio: {parent_data.get('name', 'Unknown')}", "INFO")
                    if not dry_run:
                        try:
                            # Create a copy of parent_data without any non-serializable objects
                            parent_info = {
                                'id': parent_data.get('id'),
                                'name': parent_data.get('name'),
                                'url': None,  # Add if available in your data
                                'image_path': None  # Add if available in your data
                            }
                            parent_studio = find_or_create_parent_studio(parent_info, config['stash_interface'])
                            if parent_studio and isinstance(parent_studio, dict):
                                logger(f"👆 Setting parent studio to: {parent_studio.get('name')}", "INFO")
                                studio['parent_id'] = parent_studio.get('id')
                        except Exception as e:
                            logger(f"❌ Error processing parent studio: {str(e)}", "ERROR")
                
                # Update URLs if present
                if studio_data.get('urls'):
                    for url_data in studio_data['urls']:
                        if url_data.get('type') == 'HOME' and url_data.get('url'):
                            if not studio.get('url') or force:
                                logger(f"🔗 Setting URL to: {url_data['url']}", "INFO")
                                studio['url'] = url_data['url']
                                break
                
                # Update image if present
                if studio_data.get('images') and (not studio.get('image_path') or force):
                    for image_data in studio_data['images']:
                        if image_data.get('url'):
                            logger(f"🖼️ Setting image from: {image_data['url']}", "INFO")
                            studio['image_path'] = image_data['url']
                            break
        
        except Exception as e:
            logger(f"❌ Error processing match from {endpoint_name}: {str(e)}", "ERROR")
            continue
    
    # Update the studio with new stash IDs if any were added
    if new_stash_ids != existing_stash_ids:
        if not dry_run:
            logger(f"💾 Updating studio {studio['name']} with new stash IDs", "INFO")
            try:
                # Create a copy of the studio data without the StashInterface
                studio_update = {
                    'id': studio.get('id'),
                    'name': studio.get('name'),
                    'url': studio.get('url'),
                    'parent_id': studio.get('parent_id'),
                    'image': studio.get('image_path'),  # Changed from image_path to image
                    'stash_ids': new_stash_ids
                }
                
                # Remove any None values to avoid schema validation errors
                studio_update = {k: v for k, v in studio_update.items() if v is not None}
                
                update_studio(studio_update, studio['id'], dry_run)
                logger(f"✅ Successfully updated studio {studio['name']}", "INFO")
            except Exception as e:
                logger(f"❌ Error updating studio: {str(e)}", "ERROR")
        else:
            logger(f"🔍 [DRY RUN] Would update studio {studio['name']} with new stash IDs", "INFO")
    else:
        logger(f"ℹ️ No new stash IDs to add for studio {studio['name']}", "INFO")

if __name__ == "__main__":
    main() 