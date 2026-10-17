# Reaching the WebDAV server from anywhere: ZeroTier, port forwarding, fallbacks

The WebDAV server speaks plain HTTP with Basic auth. **Never expose it directly to the internet.**
Clients reach it through an encrypted overlay network: ZeroTier by default, or Tailscale as a fallback.

## 1. ZeroTier (both machines)

These are human steps, because they need an account login:

1. Create a network at https://my.zerotier.com, sign in, and note the 16-hex-digit **Network ID**.
2. Install ZeroTier One on the server and on every client from https://www.zerotier.com/download.
   It works on Windows, macOS and Linux.
3. Join the network on each machine.
   - macOS/Linux: `sudo zerotier-cli join <NetworkID>`
   - Windows: tray icon → Join New Network
4. In my.zerotier.com → the network → Members, tick **Auth** for each machine.
   Optionally give the server a fixed managed IP and a name.

The agent can check the result with `zerotier-cli listnetworks` (status `OK`, assigned IP) and `zerotier-cli peers`.

The Zotero URL on every client is the server's ZeroTier IP plus port, for example `10.241.154.111:8080`.
This includes the server machine itself, if it also runs Zotero.

## 2. Why sync can look successful and still fail

Zotero syncs two things on two separate paths:

| What | Path | Works on a restrictive network? |
|---|---|---|
| Item metadata and full-text index | zotero.org over HTTPS 443 | yes |
| Attachment files | WebDAV, through ZeroTier UDP | **only if UDP gets through** |

On a guest or captive network such as "UCLA_WEB", the metadata syncs and the sync icon looks normal, but no file is uploaded.
The other machine then receives items whose files never come. The WebDAV server log shows **no requests at all** from that client during that period.
UCLA's authenticated network (UCLA_WIFI / eduroam) worked; the guest network did not.

## 3. Port forwarding on the server's home network

Port forwarding gives the server a fixed, reachable UDP port, so a client behind a symmetric NAT (campus or hotel network) can open a direct path to it.
It **does not help if the client network blocks UDP outright**; for that case, see §4.

### 3a. Detect the NAT layers (the agent can do this)

```
# macOS (pick the Wi-Fi/Ethernet interface; -i bypasses any VPN tunnel)
traceroute -n -m 4 -w 1 -q 1 -i en0 1.1.1.1
# Linux:   traceroute -n -m 4 -w 1 -q 1 -i <iface> 1.1.1.1
# Windows: tracert -d -h 4 1.1.1.1
```

Count the private hops (10.x, 172.16–31.x, 192.168.x) before the first public IP. Each private hop is one router that needs a forwarding rule.

- The first public hop must not be in `100.64.0.0/10`. If it is, the ISP uses carrier-grade NAT and port forwarding is impossible; go to §4.
- The router's MAC prefix often identifies it. For example `94:83:c4` is GL.iNet, whose admin page is at `192.168.8.1`.

### 3b. Human steps (router admin passwords: the agent must not type them)

Example from the reference deployment, which has **two layers**:

```
server 192.168.8.225 → GL.iNet 192.168.8.1 → ISP router 192.168.1.1 → public IP
```

1. **Inner router (GL.iNet, http://192.168.8.1):**
   - Network → LAN → Address Reservation: bind the server's MAC to its IP, e.g. `192.168.8.225`.
   - Network → Port Forwarding → Add: protocol **UDP**, external port **9993**, internal IP **server IP**, internal port **9993**, enabled.
   - Note the router's **WAN IP** (`192.168.1.x`) from the dashboard.
2. **Outer router (ISP).** For Spectrum, use the My Spectrum app:
   - Services → Router (or Equipment) → Advanced Settings → Port Forwarding & IP Reservations.
   - Pick the GL.iNet device and reserve its IP.
   - Add Port Assignment: UDP, 9993 → 9993.
   - Other ISP routers: open their web UI at the gateway IP and add the same rule. DMZ pointing at the inner router's WAN IP also works.
   - With a single router, only one rule is needed: UDP 9993 → server IP.

Router changes may reboot the modem and change the public IP. That is harmless, because ZeroTier finds the new address through its root servers.

### 3c. Verify

- **On the server (the agent can check):**
  ```
  zerotier-cli -j info | python3 -c "import json,sys;print(json.load(sys.stdin)['config']['settings']['surfaceAddresses'])"
  ```
  A `<public IP>/9993` entry means the outside world sees port 9993, so the forwarding works.
- **The real test, on the client while it is on the restrictive network:** run `zerotier-cli peers` and find the server's node ID (`zerotier-cli info` on the server prints it).
  - `DIRECT <public IP>/9993` → fixed.
  - `RELAY`, or no path, or `zerotier-cli info` showing `TUNNELED` or `OFFLINE` → that network blocks UDP. Go to §4.
- **Map a ZeroTier IP to its node ID** when a peer is unknown. ZeroTier MACs encode the node ID: take the MAC from `arp -n <ZT IP>`, then
  ```
  python3 -c "nw=0x<NETWORKID>; mac=0x<MAC without colons>; x=((nw>>8)&0xff)<<32|((nw>>16)&0xff)<<24|((nw>>24)&0xff)<<16|((nw>>32)&0xff)<<8|((nw>>40)&0xff); print(hex((mac^x)&0xffffffffff))"
  ```

## 4. When UDP is blocked

Pick one:

- **Use the network that allows it**, for example UCLA_WIFI instead of UCLA_WEB. This is the simplest option.
- **Tailscale**, alongside or instead of ZeroTier. When UDP is blocked it relays over HTTPS 443 (DERP), so it works on almost any network.
  1. Install it on both machines and sign in. This is a human step.
  2. Point the *roaming* client's Zotero WebDAV URL at the server's Tailscale IP or MagicDNS name, port 8080.
  3. Changing the URL makes Zotero recheck every file once (`resetAllSyncStates`). That is expected and harmless.
  4. If the server binds rclone to one address (`--bind`), bind it to `0.0.0.0` or run a second instance, so that both overlays reach it.
- **Do not** put the plain-HTTP WebDAV port on the public internet. If public access is really needed, put it behind HTTPS (Caddy, nginx or Cloudflare Tunnel) and keep the auth proxy.

## 5. A VPN on the server itself

If the server machine runs a full-tunnel VPN, its default route goes through a `utun`/`tun` interface, and `curl https://api.ipify.org` shows the VPN's IP.
ZeroTier normally still uses the physical interface. Check `surfaceAddresses`: the home public IP should appear there.
If only the VPN's IP appears, the port forwarding cannot work while the VPN is on.
