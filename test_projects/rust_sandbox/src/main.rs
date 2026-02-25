mod math;

use math::{distance_squared, Point};

fn greet(name: &str) -> String {
    format!("Hello, {name}!")
}

fn main() {
    let mut origin = Point::new(0, 0);
    let p = Point::new(3, 4);

    println!("{}", greet("Rust"));
    println!("distance^2 = {}", distance_squared(&origin, &p));

    // Uncomment to test diagnostics:
    // let broken: i32 = "not an int";

    origin.translate(1, 1);
    println!("moved origin = {:?}", origin);
}
