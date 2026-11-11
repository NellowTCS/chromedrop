//! ChromeOS has no polkit, but `owly` asks org.freedesktop.PolicyKit1 for
//! permission before bringing the radio up. This answers "yes" to everything.

// (Normally a security risk, but ChromeOS relies on its own system daemons (like shill) 
// to manage hardware access)

use std::collections::HashMap;
use zbus::{blocking::connection::Builder, interface, zvariant::OwnedValue};

struct Authority;

#[interface(name = "org.freedesktop.PolicyKit1.Authority")]
impl Authority {
    fn check_authorization(
        &self,
        _subject: (String, HashMap<String, OwnedValue>),
        _action_id: String,
        _details: HashMap<String, String>,
        _flags: u32,
        _cancellation_id: String,
    ) -> (bool, bool, HashMap<String, String>) {
        (true, false, HashMap::new())
    }
}

fn main() -> zbus::Result<()> {
    let _conn = Builder::system()?
        .name("org.freedesktop.PolicyKit1")?
        .serve_at("/org/freedesktop/PolicyKit1/Authority", Authority)?
        .build()?;
    loop {
        std::thread::park();
    }
}
