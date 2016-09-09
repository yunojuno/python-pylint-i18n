This is a pylint checker to check for strings in python source code that has not been passed through ugettext.


It is based on the parent fork from @rory (writeup [here](http://www.technomancy.org/python/pylint-i18n-lint-checker/) but has been updated to plug into YJ's workflow more specifically. 

Invoke it with

`pylint some_module.py --load-plugins=missing_gettext --disable=all --enable=missing_gettext --whitelist-single-quoted=y`

The `whitelist-single-quoted` option is probably only useful if you have a house style where keys and other never-to-be-translated strings are in single quotes (and you've got enough code review happening to protect against infractions) 
