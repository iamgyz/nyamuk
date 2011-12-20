'''
Nyamuk
Python Mosquitto Client Library
@author : Iwan Budi Kusnanto <iwan.b.kusnanto@gmail.com>
'''
import socket
import select
import time
import sys
import errno

from mqtt_pkt import MqttPkt
from MV import MV
import nyamuk_net
from nyamuk_msg import NyamukMsg, NyamukMsgAll

MQTTCONNECT = 16# 1 << 4
class BaseNyamuk:
    def __init__(self, id = None, username = None, password = None, WITH_BROKER = False):
        ''' Constructor '''
        self.id = id
        self.username = None
        self.password = None
        
        self.address = ""
        self.keep_alive = MV.KEEPALIVE_VAL
        self.clean_session = False
        self.state = MV.CS_NEW
        self.last_msg_in = time.time()
        self.last_msg_out = time.time()
        self.last_mid = 0
        
        #output packet queue
        self.out_packet = []
        
        #input packet queue
        self.in_packet = MqttPkt()
        self.in_packet.packet_cleanup()
        
        #networking
        self.sock = MV.INVALID_SOCKET
        
        self.will = None
        
        
        self.in_callback = False
        self.message_retry = MV.MESSAGE_RETRY
        self.last_retry_check = 0
        self.messages = None
        
        
        #LOGGING:TODO
        self.log_priorities = -1
        self.log_destinations = -1
        
        #callback
        self.on_connect = None
        self.on_disconnect = None
        self.on_message = None
        self.on_publish = None
        self.on_subscribe = None
        self.on_unsubscribe = None
        
        self.host = None
        self.port = 1883
        
        #hack var
        self.as_broker = False
    
    def __del__(self):
        pass
        
    def mid_generate(self):
        self.last_mid += 1
        if self.last_mid == 0:
            self.last_mid += 1
        return self.last_mid
    
    def packet_queue(self, pkt):
        '''
        Enqueue packet to out_packet queue
        '''
        
        pkt.pos = 0
        pkt.to_process = pkt.packet_length
        
        self.out_packet.append(pkt)
        
        #if self.in_callback == False:
        #    return self.packet_write()
        #else:
        #    return MV.ERR_SUCCESS
        
        return MV.ERR_SUCCESS
    
    def packet_write(self):
        """Write packet to network."""
        if self.sock == MV.INVALID_SOCKET:
            return MV.ERR_NO_CONN
        
        while len(self.out_packet) > 0:
            pkt = self.out_packet[0]
            write_length, status = nyamuk_net.write(self.sock, pkt.payload)
            if write_length > 0:
                pkt.to_process -= write_length
                pkt.pos += write_length
                
                if pkt.to_process > 0:
                    return MV.ERR_SUCCESS
            else:
                if status == errno.EAGAIN or status == errno.EWOULDBLOCK:
                    return MV.ERR_SUCCESS
                elif status == errno.ECONNRESET:
                    return MV.ERR_CONN_LOST
                else:
                    return MV.ERR_UNKNOWN
            
            if pkt.command & 0xF6 == MV.CMD_PUBLISH and self.on_publish is not None:
                self.in_callback = True
                self.on_publish(pkt.mid)
                self.in_callback = False
            
            #next
            del self.out_packet[0]
            
            #free data (unnecessary)
            
            self.last_msg_out = time.time()
            
        
        return MV.ERR_SUCCESS
    
    def packet_read(self):
        """Read packet from network."""
        if self.sock == MV.INVALID_SOCKET:
            return MV.ERR_NO_CONN
        
        if self.in_packet.command == 0:
            readlen, ba,status = nyamuk_net.read(self.sock, 1)
            if readlen == 1:
                byte = ba[0]
                self.in_packet.command = byte
                
                if self.as_broker == True:
                    if self.bridge is None and self.state == MV.CS_NEW and (byte & 0xF0) != MV.CMD_CONNECT:
                        print "RETURN ERR_PROTOCOL"
                        return MV.ERR_PROTOCOL
            else:
                if readlen == 0:
                    return MV.ERR_CONN_LOST
                if status == errno.EAGAIN or status == errno.EWOULDBLOCK:
                    return MV.ERR_SUCCESS
                else:
                    if status == errno.ECONNRESET:
                        return MV.ERR_CONN_LOST
                    else:
                        return MV.ERR_UNKNOWN
        
        if self.in_packet.have_remaining == False:
            loop_flag = True
            while loop_flag == True:
                readlen, ba,status = nyamuk_net.read(self.sock, 1)
                byte = ba[0]
                if readlen == 1:
                    self.in_packet.remaining_count += 1
                    if self.in_packet.remaining_count > 4:
                        return MV.ERR_PROTOCOL
                    
                    self.in_packet.remaining_length += (byte & 127) * self.in_packet.remaining_mult
                    self.in_packet.remaining_mult *= 128
                else:
                    if readlen == 0:
                        return MV.ERR_CONN_LOST
                
                if (byte & 128) == 0:
                    loop_flag = False
            
            if self.in_packet.remaining_length > 0:
                self.in_packet.payload = bytearray(self.in_packet.remaining_length)
                if self.in_packet.payload is None:
                    return MV.ERR_NO_MEM
                self.in_packet.to_process = self.in_packet.remaining_length
            
            self.in_packet.have_remaining = True
        
        if self.in_packet.to_process > 0:
            readlen, ba, status = nyamuk_net.read(self.sock, self.in_packet.to_process)
            if readlen > 0:
                for x in range(0, readlen):
                    self.in_packet.payload[self.in_packet.pos] = ba[x]
                    self.in_packet.pos += 1
                    self.in_packet.to_process -= 1
            else:
                if status == errno.EAGAIN or status == errno.EWOULDBLOCK:
                    return MV.ERR_SUCCESS
                else:
                    if status == errno.ECONNRESET:
                        return MV.ERR_CONN_LOST
                    else:
                        return MV.ERR_UNKNOWN
        
        #all data for this packet is read
        self.in_packet.pos = 0
        
        rc = self.packet_handle()
        
        self.in_packet.packet_cleanup()
        
        self.last_msg_in = time.time()
        
        return rc
                
    def socket_close(self):
        """Close our socket."""
        if self.sock != MV.INVALID_SOCKET:
            self.sock.close()
        self.sock = MV.INVALID_SOCKET
        
    def build_publish_pkt(self, mid, topic, payload, qos, retain, dup):
        """Build PUBLISH packet."""
        pkt = MqttPkt()
        payloadlen = len(payload)
        packetlen = 2 + len(topic) + payloadlen
        
        if qos > 0:
            packetlen += 2
        
        pkt.mid = mid
        pkt.command = MV.CMD_PUBLISH | ((dup & 0x1) << 3) | (qos << 1) | retain
        pkt.remaining_length = packetlen
        
        rc = pkt.alloc()
        if rc != MV.ERR_SUCCESS:
            return rc, None
        
        #variable header : Topic String
        pkt.write_string(topic, len(topic))
        
        if qos > 0:
            pkt.write_uint16(mid)
        
        #payloadlen
        if payloadlen > 0:
            pkt.write_bytes(payload, payloadlen)
        
        return MV.ERR_SUCCESS, pkt
    
    def send_simple_command(self, cmd):
        pkt = MqttPkt()
        
        pkt.command = cmd
        pkt.remaining_length = 0
        
        rc = pkt.alloc()
        if rc != MV.ERR_SUCCESS:
            return rc
        
        return self.packet_queue(pkt)