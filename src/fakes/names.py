"""Имена blob-ов, которые знает сам winws2 (nfqws2), и аргументы-ссылки на blob.

Это правила самого движка, а не данные реестра:

- ``NFQWS2_BUILTIN_BLOBS`` nfqws2 загружает сам при старте
  (``load_const_blob_to_collection`` в nfq2/nfqws.c). Повторное объявление
  ``--blob=fake_default_tls:...`` winws2 отвергает ошибкой
  «duplicate blob name», поэтому в реестре и в ``--blob=`` их быть не должно;
- ``BLOB_REFERENCE_ARG_NAMES`` — аргументы ``--lua-desync``, значение которых
  является именем blob-а (или hex-литералом ``0x..``).
"""

from __future__ import annotations


NFQWS2_BUILTIN_BLOBS: frozenset[str] = frozenset(
    {"fake_default_tls", "fake_default_http", "fake_default_quic"}
)

BLOB_REFERENCE_ARG_NAMES: frozenset[str] = frozenset(
    {"blob", "fake_blob", "pattern", "seqovl_pattern", "fallback"}
)

# ``--blob=ИМЯ:...`` кладёт байты в глобальную переменную Lua с этим именем
# (``lua_init_blobs`` в nfq2/lua.c), причём ДО констант и функций nfqws2.
# Поэтому имя своего фейка не должно совпадать:
# - с ключевыми словами и стандартной библиотекой Lua — иначе фейк затрёт,
#   например, ``string`` или ``table``, и lua-скрипты сломаются;
# - с константами и функциями, которые nfqws2 регистрирует сам
#   (``lua_init_const`` / ``lua_init_functions`` в nfq2/lua.c) — они затрут фейк.
# Имена из файлов --lua-init считаются отдельно, прямо из этих файлов.
_LUA_KEYWORDS_AND_STDLIB = """
and break do else elseif end false for function goto if in local nil not or
repeat return then true until while
_G _VERSION _ENV arg assert bit bit32 collectgarbage coroutine debug dofile
error ffi getfenv getmetatable io ipairs jit load loadfile loadstring math
module next os package pairs pcall print rawequal rawget rawlen rawset require
select setfenv setmetatable string table tonumber tostring type unpack utf8
xpcall
"""

_NFQWS2_LUA_GLOBALS = """
NFQWS2_VER NFQWS2_COMPAT_VER qnum divert_port desync_fwmark
DEFAULT_MSS ICMP6_DST_UNREACH ICMP6_DST_UNREACH_ADDR
ICMP6_DST_UNREACH_ADMIN ICMP6_DST_UNREACH_BEYONDSCOPE
ICMP6_DST_UNREACH_NOPORT ICMP6_DST_UNREACH_NOROUTE ICMP6_ECHO_REPLY
ICMP6_ECHO_REQUEST ICMP6_PACKET_TOO_BIG ICMP6_PARAM_PROB
ICMP6_PARAMPROB_HEADER ICMP6_PARAMPROB_NEXTHEADER ICMP6_PARAMPROB_OPTION
ICMP6_TIME_EXCEEDED ICMP6_TIME_EXCEED_REASSEMBLY ICMP6_TIME_EXCEED_TRANSIT
ICMP_BASE_LEN ICMP_DEST_UNREACH ICMP_ECHO ICMP_ECHOREPLY ICMP_INFO_REPLY
ICMP_INFO_REQUEST ICMP_PARAMETERPROB ICMP_REDIRECT ICMP_REDIRECT_HOST
ICMP_REDIRECT_NET ICMP_REDIRECT_TOSHOST ICMP_REDIRECT_TOSNET
ICMP_TIME_EXCEEDED ICMP_TIMESTAMP ICMP_TIMESTAMPREPLY ICMP_TIMXCEED_INTRANS
ICMP_TIMXCEED_REASS ICMP_UNREACH_FILTER_PROHIB ICMP_UNREACH_HOST
ICMP_UNREACH_HOST_PRECEDENCE ICMP_UNREACH_HOST_PROHIB
ICMP_UNREACH_HOST_UNKNOWN ICMP_UNREACH_NEEDFRAG ICMP_UNREACH_NET
ICMP_UNREACH_NET_PROHIB ICMP_UNREACH_NET_UNKNOWN ICMP_UNREACH_PORT
ICMP_UNREACH_PRECEDENCE_CUTOFF ICMP_UNREACH_PROTOCOL ICMP_UNREACH_SRCFAIL
ICMP_UNREACH_TOSHOST ICMP_UNREACH_TOSNET IP6_BASE_LEN IP6F_MORE_FRAG
IP_BASE_LEN IP_DF IP_FLAGMASK IP_MF IP_OFFMASK IPPROTO_AH IPPROTO_DSTOPTS
IPPROTO_ESP IPPROTO_FRAGMENT IPPROTO_HIP IPPROTO_HOPOPTS IPPROTO_ICMP
IPPROTO_ICMPV6 IPPROTO_IP IPPROTO_IPIP IPPROTO_IPV6 IPPROTO_MH IPPROTO_NONE
IPPROTO_ROUTING IPPROTO_SCTP IPPROTO_SHIM6 IPPROTO_TCP IPPROTO_UDP IP_RF
IPTOS_DSCP_MASK IPTOS_ECN_CE IPTOS_ECN_ECT0 IPTOS_ECN_ECT1 IPTOS_ECN_MASK
IPTOS_ECN_NOT_ECT IPV6_FLOWINFO_MASK IPV6_FLOWLABEL_MASK MLD_LISTENER_QUERY
MLD_LISTENER_REDUCTION MLD_LISTENER_REPORT ND_NEIGHBOR_ADVERT
ND_NEIGHBOR_SOLICIT ND_REDIRECT ND_ROUTER_ADVERT ND_ROUTER_SOLICIT
NFQWS2_COMPAT_VER TCP_BASE_LEN TCP_KIND_AO TCP_KIND_END TCP_KIND_FASTOPEN
TCP_KIND_MD5 TCP_KIND_MSS TCP_KIND_NOOP TCP_KIND_SACK TCP_KIND_SACK_PERM
TCP_KIND_SCALE TCP_KIND_TS TH_ACK TH_CWR TH_ECE TH_FIN TH_PUSH TH_RST
TH_SYN TH_URG UDP_BASE_LEN VERDICT_DROP VERDICT_MASK VERDICT_MODIFY
VERDICT_PASS VERDICT_PRESERVE_NEXT
b_ctrack_disable b_daemon b_debug b_ipcache_hostname b_server
aes aes_ctr aes_gcm band bcryptorandom bitand bitget bitlshift bitnot bitnot16
bitnot24 bitnot32 bitnot48 bitnot8 bitor bitrshift bitset bitxor bor brandom
brandom_az brandom_az09 bu16 bu24 bu32 bu48 bu8 bxor clock_getfloattime
clock_gettime conntrack_feed csum_icmp_fix csum_ip4_fix csum_tcp_fix
csum_udp_fix dissect dissect_icmphdr dissect_ip6hdr dissect_iphdr
dissect_tcphdr dissect_udphdr divint DLOG DLOG_CONDUP DLOG_ERR execution_plan
execution_plan_cancel get_ifaddrs getpid get_source_ip gettid gmtime
gunzip_end gunzip_inflate gunzip_init gzip_deflate gzip_end gzip_init hash
hkdf instance_cutoff localtime lua_cutoff memcpy ntop parse_hex pton
raw_packet rawsend rawsend_dissect reconstruct_dissect reconstruct_icmphdr
reconstruct_ip6hdr reconstruct_iphdr reconstruct_tcphdr reconstruct_udphdr
resolve_multi_pos resolve_pos resolve_range stat swap16 swap24 swap32 swap48
timegm timelocal timer_del timer_enum timer_info timer_set tls_mod u16 u16add
u24 u24add u32 u32add u48 u48add u8 u8add uname
"""

NFQWS2_LUA_RESERVED_NAMES: frozenset[str] = frozenset(
    (_LUA_KEYWORDS_AND_STDLIB + _NFQWS2_LUA_GLOBALS).split()
)


__all__ = ["BLOB_REFERENCE_ARG_NAMES", "NFQWS2_BUILTIN_BLOBS", "NFQWS2_LUA_RESERVED_NAMES"]
