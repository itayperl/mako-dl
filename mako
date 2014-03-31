#!/usr/bin/env python
import logging
import libmako
import argparse
from libmako import logger

def setup_logging(level):
    log_handler = logging.StreamHandler()
    log_handler.setLevel(level)
    logger.addHandler(log_handler)
    logger.setLevel(level)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url', help='Mako VOD URL (program index, program page or episode page)', nargs='?', default='/mako-vod-index')
    parser.add_argument('-d', '--debug', default='error', choices=['debug', 'info', 'warn', 'error'],
                                         help='Enable debug output')
    libmako.add_selection_option(parser, '-s', '--select')
    parser.add_argument('-l', '--list', action='store_true', help="Don't download anything -- only display a list")
    parser.add_argument('-o', '--output', metavar='DIR', help='Output directory', default='.')
    args = parser.parse_args()

    setup_logging(getattr(logging, args.debug.upper()))
    libmako.process_url(args.url, selection=args.select, output=args.output, download=not args.list)

if __name__ == '__main__':
    main()
