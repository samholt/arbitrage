# Copyright (C) 2017, Kirill Bespalov <k.besplv@gmail.com>

import json
import logging
import time

import tenacity
import pika
import requests
import random
import string
import base64
#import configparser

from pika.exceptions import AMQPError, AMQPChannelError
from tenacity import stop_after_delay, wait_exponential
from arbitrage.observers.observer import ObserverBase
from Crypto.Cipher import AES

LOG = logging.getLogger(__name__)


class AMQPClient(object):
    """Represents the AMQP client to send an opportunity given by watcher"""

    def __init__(self, config):
        self.config = config
        self.message_ttl = str(self.config.market_expiration_time * 1000)
        self.report_queue = self.config.report_queue
        self.params = pika.URLParameters(self.config.amqp_url)
        self.queue_args = self.config.queue_args
        self._connection = None
        self._channel = None
        ##app_config = configparser.ConfigParser()
        ##app_config.read('config')
        self.key = config.creds['settings']['aes_key']

    def _queue_exists(self):
        """Check if the queue exists"""
        try:
            self._channel.queue_declare(self.config.report_queue, passive=True)
        except AMQPChannelError as e:
            # restore a channel
            self._channel = self._connection.channel()
            code = e.args[0]
            if code == 404:
                return False
            else:
                raise
        return True

    def ensure_connected(self):
        """Ensure that connection is established and the queue is declared"""
        try:
            if not self._connection or not self._connection.is_open:
                self._connection = pika.BlockingConnection(self.params)
            if not self._channel or not self._connection.is_open:
                self._channel = self._connection.channel()
                if not self._queue_exists():
                    self._channel.queue_declare(self.config.report_queue,
                                                arguments=self.queue_args)

        except AMQPError as e:
            LOG.error('Failed to establish connection. Retrying %s' % e)
            raise
        LOG.debug('AMQP connection to %s was successfully established' %
                  self.config.amqp_url)
        return True

    @property
    def channel(self):
        if not self._connection or not self._channel.is_open:
            stop = stop_after_delay(self.config.market_expiration_time)
            wait = wait_exponential(max=5)
            retry = tenacity.retry(stop=stop, wait=wait)
            retry(self.ensure_connected)()

        return self._channel

    def push(self, data):
        """Push a data to the exchange"""
        try:
            properties = pika.BasicProperties(
                content_type='application/json',
                expiration=self.message_ttl,
                timestamp=int(time.time())
            )

            #https://stackoverflow.com/questions/2257441/random-string-generation-with-upper-case-letters-and-digits-in-python
            init_vector = ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(16))

            #encryption_suite = AES.new(self.key, AES.MODE_CFB, init_vector, segment_size=128)
            encryption_suite = AES.new(self.key, AES.MODE_CFB, init_vector)
            json_data = json.dumps(data)

            # encrypt returns an encrypted byte string
            cipher_text = encryption_suite.encrypt(json_data)

            # encrypted byte string is base 64 encoded for message passing
            base64_cipher_byte_string = base64.b64encode(cipher_text)

            # base 64 byte string is decoded to utf-8 encoded string for json serialization
            base64_cipher_string = base64_cipher_byte_string.decode('utf-8')

            data = {"iv": init_vector,
                    "encrypted_data": base64_cipher_string}

            self.channel.basic_publish(exchange='',
                                       routing_key=self.report_queue,
                                       body=json.dumps(data),
                                       properties=properties)
        except Exception as e:
            LOG.error('Failed to push a message %s. Skipped.' % data)
            LOG.error('Exception %s.' % e)


class Rabbitmq(ObserverBase):
    """Represent AMQP based arbitrage opportunity observer"""

    def __init__(self, config):
        super().__init__(config)
        self.client = AMQPClient(config)

    def opportunity(self, profit, volume, buyprice, kask, sellprice, kbid,
                    perc, weighted_buyprice, weighted_sellprice, max_buy_price, min_sell_price):
        """Sends opportunity to a message queue"""
        LOG.debug("sending message to queue")
        # split market name and currency:  KrakenUSD -> (Kraken, USD)
        buy_exchange, buy_currency = kask[:-3], kask[-3:]
        sell_exchange, sell_currency = kbid[:-3], kbid[-3:]

        if sell_currency != buy_currency:
            LOG.info("Sell currency not equal to buy currency")
            return(0)

        watch_currency = sell_currency


        if watch_currency == "DSH":
            watch_currency = "DASH"

        creds = self.client.config.creds

        #order = {
        #    "params":{
        #        "order_type": "inter_exchange_arb" #_id":
        #        "base_currency": 1#base_currency #_id":
        #        "quote_currency": 1#quote_currency #_id":
        #        "direction": "BID"
        #        "price": weighted_buyprice
        #        "volume": volume
        #    },
        #    "user":{
        #        "id": 1
        #        "investment_strategy":1
        #    }
        #    "exchange":{
        #        "key": 1
        #        "secret": 1
        #        "passphrase": 1
        #    }
        #}

        investor_currency = "BTC"
        max_tx_volume = 0.005


        buy_base_currency = watch_currency
        buy_quote_currency = "BTC"
        sell_base_currency = watch_currency
        sell_quote_currency = "BTC"

        if watch_currency == "USD" or watch_currency == "EUR":
            buy_base_currency = "BTC"
            sell_base_currency = "BTC"
            buy_quote_currency = watch_currency
            sell_quote_currency = watch_currency

        if buy_base_currency == investor_currency:
            buy_volume = min([volume, max_tx_volume])
            e_profit = (weighted_sellprice - weighted_buyprice) * buy_volume
            limit_profit = (min_sell_price - max_buy_price) * buy_volume
            v_a_r = buy_volume
        elif buy_quote_currency == investor_currency:
            buy_volume = min([volume, max_tx_volume/max_buy_price])
            e_profit = ((weighted_sellprice - weighted_buyprice) * buy_volume)/weighted_buyprice
            limit_profit = ((min_sell_price - max_buy_price) * buy_volume)/max_buy_price
            v_a_r = buy_volume*max_buy_price
        else:
            LOG.info("investor_currency not represented in arbitrage_opportunity")
            return(0)

        sell_volume = buy_volume

        e_roi = weighted_sellprice/weighted_buyprice-1.0
        limit_roi = min_sell_price/max_buy_price-1.0

        "Sammy ate {0:.3f} percent of a pizza!".format(75.765367)


        LOG.info("Expected Profit (Limit Profit): "+str(e_profit)+" ("+str(limit_profit)+") "+investor_currency)
        LOG.info("Expected ROI (Limit ROI): "+"{0:.2f}".format(e_roi*100)+" ("+"{0:.2f}".format(limit_roi*100)+") %")
        LOG.info("Value at Risk: "+str(v_a_r)+" "+investor_currency)
        LOG.info("BUY (BID) "+str(buy_volume)+" "+buy_base_currency+" @ "+str(max_buy_price)+" "+buy_quote_currency+"/"+buy_base_currency+" on "+buy_exchange.upper())
        LOG.info("SELL (ASK) "+str(sell_volume)+" "+sell_base_currency+" @ "+str(min_sell_price)+" "+sell_quote_currency+"/"+sell_base_currency+" on "+sell_exchange.upper())

        #if buy_base_currency == base_currency:
            # implement volume limit on
            
        data = {
            "api_key":self.client.config.api_key,
            "buy_currency": buy_base_currency,
            "buy_exchange": buy_exchange,
            "sell_currency": sell_base_currency,
            "sell_exchange": sell_exchange,
        }
        
        request = requests.post(self.client.config.api_endpoint, data=data)
        responses = json.loads(request.content.decode('utf8'))['data']
        LOG.debug(responses)
        #for account in requests.content:
            
        test_responses = [{
            "buy_balance": buy_volume,
            "sell_balance": sell_volume,
            "user_id": 9,
            "investment_strategy_id": 2,
            "sell_exchange_key": "xxxx",
            "sell_exchange_secret": "xxxx",
            "sell_exchange_passphrase": "xxxx",
            "buy_exchange_key": "xxxx",
            "buy_exchange_secret": "xxxx",
            "buy_exchange_passphrase": "xxxx"
        }]
        
        for response in responses:
            
            user_buy_volume = response['buy_balance']
            user_sell_volume = response['sell_balance']
            
            message = {"order_type": "inter_exchange_arb",
                       "order_specs": {
                           "buy_base_currency": buy_base_currency,
                           "buy_quote_currency": buy_quote_currency,
                           "buy_volume": user_buy_volume,
                           "buy_price": max_buy_price,
                           "buy_exchange": buy_exchange.upper(),
                           "sell_base_currency": sell_base_currency,
                           "sell_quote_currency": sell_quote_currency,
                           "sell_volume": user_sell_volume,
                           "sell_price": min_sell_price,
                           "sell_exchange": sell_exchange.upper()},
                        "user_specs": {
                            "user_id": response['user_id'],
                            "investment_strategy_id": response['investment_strategy_id'],
                           "sell_exchange_key": response['sell_exchange_key'],
                           "sell_exchange_secret": response['sell_exchange_secret'],
                           "sell_exchange_passphrase": response['sell_exchange_passphrase'],
                           "buy_exchange_key": response['buy_exchange_key'],
                           "buy_exchange_secret": response['buy_exchange_secret'],
                           "buy_exchange_passphrase": response['buy_exchange_passphrase']
                       },
                   }
            LOG.info("sending message")
            self.client.push(message)
