"""What every newly stored ride gets, whichever way it arrived (Garmin sync, file import, phone upload): matched against segments, and checked by the
sanity rails. One place, so a new import path cannot forget one of them. Never raises; does not commit."""


def after_ride_added(db, act_id):
    from services import sanity
    from services.segments import scan_new_ride
    try:
        sanity.flag(db, act_id)
    except Exception:
        pass
    scan_new_ride(db, act_id)
