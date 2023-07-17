import asyncio
from tcputils import *
import random
from collections import OrderedDict

class Servidor:
    def __init__(self, rede, porta):
        self.rede = rede
        self.porta = porta
        self.conexoes = {}
        self.callback = None
        self.rede.registrar_recebedor(self._rdt_rcv)

    def registrar_monitor_de_conexoes_aceitas(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que uma nova conexão for aceita
        """
        self.callback = callback

    def _rdt_rcv(self, src_addr, dst_addr, segment):
        src_port, dst_port, seq_no, ack_no, \
            flags, window_size, checksum, urg_ptr = read_header(segment)

        if dst_port != self.porta:
            # Ignora unacked que não são destinados à porta do nosso servidor
            return
        if not self.rede.ignore_checksum and calc_checksum(segment, src_addr, dst_addr) != 0:
            print('descartando segmento com checksum incorreto')
            return

        payload = segment[4*(flags>>12):]
        id_conexao = (src_addr, src_port, dst_addr, dst_port)

        if (flags & FLAGS_SYN) == FLAGS_SYN:
            # A flag SYN estar setada significa que é um cliente tentando estabelecer uma conexão nova
            # TODO: talvez você precise passar mais coisas para o construtor de conexão
            conexao = self.conexoes[id_conexao] = Conexao(self, id_conexao, seq_no)
            # TODO: você precisa fazer o handshake aceitando a conexão. Escolha se você acha melhor
            # fazer aqui mesmo ou dentro da classe Conexao.
            if self.callback:
                self.callback(conexao)
        elif id_conexao in self.conexoes:
            # Passa para a conexão adequada se ela já estiver estabelecida
            self.conexoes[id_conexao]._rdt_rcv(seq_no, ack_no, flags, payload)
        else:
            print('%s:%d -> %s:%d (pacote associado a conexão desconhecida)' %
                  (src_addr, src_port, dst_addr, dst_port))


class Conexao:
    def __init__(self, servidor, id_conexao, seq_no):
        self.servidor = servidor
        self.id_conexao = id_conexao
        self.callback = None
        self.seq_no = random.randint(0, 0xffff)
        self.ack_no = seq_no + 1
        self.fin = False
        self.unacked = OrderedDict()
        src_addr, src_port, dst_addr, dst_port = id_conexao
        self.servidor.rede.enviar(fix_checksum(make_header(dst_port, src_port, self.seq_no, self.ack_no, FLAGS_SYN + FLAGS_ACK), dst_addr, src_addr), src_addr)
        self.seq_no += 1
        self.base_seq = self.seq_no
        self.timer = None
        #self.timer.cancel()   # é possível cancelar o timer chamando esse método; esta linha é só um exemplo e pode ser removida

    def retransmitir(self):
        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        key = list(self.unacked.keys())[0]
        self.servidor.rede.enviar(fix_checksum(make_header(dst_port, src_port, key, self.ack_no, FLAGS_ACK) + self.unacked[key], dst_addr, src_addr), src_addr)

    def _rdt_rcv(self, seq_no, ack_no, flags, payload):
        # TODO: trate aqui o recebimento de unacked provenientes da camada de rede.
        # Chame self.callback(self, dados) para passar dados para a camada de aplicação após
        # garantir que eles não sejam duplicados e que tenham sido recebidos em ordem.
        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        if (flags & FLAGS_FIN) == FLAGS_FIN:
            self.callback(self, b'')
            self.ack_no = seq_no + 1
            self.servidor.rede.enviar(fix_checksum(make_header(dst_port, src_port, self.seq_no, self.ack_no, FLAGS_ACK), dst_addr, src_addr), src_addr)
        if (flags & FLAGS_ACK) == FLAGS_ACK:
            if self.fin and ack_no == self.seq_no + 1:
                del self.servidor.conexoes[self.id_conexao]
            elif ack_no > self.base_seq and len(self.unacked) > 0:
                while len(self.unacked) > 0 and self.base_seq < ack_no:
                    self.base_seq += len(self.unacked[list(self.unacked.keys())[0]])
                    del self.unacked[list(self.unacked.keys())[0]]
                if self.timer != None:
                    if len(self.unacked) > 0:
                        self.timer = asyncio.get_event_loop().call_later(1, self.retransmitir)
                    else:
                        self.timer.cancel()
        if seq_no == self.ack_no and len(payload) > 0:
            self.callback(self, payload)
            self.ack_no += len(payload)
            self.servidor.rede.enviar(fix_checksum(make_header(dst_port, src_port, self.seq_no, self.ack_no, FLAGS_ACK), dst_addr, src_addr), src_addr)
            print('recebido payload: %r' % payload)

    # Os métodos abaixo fazem parte da API

    def registrar_recebedor(self, callback):
        """
        Usado pela camada de aplicação para registrar uma função para ser chamada
        sempre que dados forem corretamente recebidos
        """
        self.callback = callback

    def enviar(self, dados):
        """
        Usado pela camada de aplicação para enviar dados
        """
        # TODO: implemente aqui o envio de dados.
        # Chame self.servidor.rede.enviar(segmento, dest_addr) para enviar o segmento
        # que você construir para a camada de rede.
        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        self.base_seq = self.seq_no
        for i in range(0, len(dados), MSS):
            self.unacked[self.seq_no] = dados[i:i + MSS]
            self.seq_no += len(dados[i:i + MSS])
        for key in self.unacked.keys():
            self.servidor.rede.enviar(fix_checksum(make_header(dst_port, src_port, key, self.ack_no, FLAGS_ACK) + self.unacked[key], dst_addr, src_addr), src_addr)
        if self.timer == None:
            self.timer = asyncio.get_event_loop().call_later(1, self.retransmitir)
        pass

    def fechar(self):
        """
        Usado pela camada de aplicação para fechar a conexão
        """
        # TODO: implemente aqui o fechamento de conexão
        src_addr, src_port, dst_addr, dst_port = self.id_conexao
        self.servidor.rede.enviar(fix_checksum(make_header(dst_port, src_port, self.seq_no, self.ack_no, FLAGS_FIN), dst_addr, src_addr), src_addr)
        self.fin = True
        pass