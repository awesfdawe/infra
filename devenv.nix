{ pkgs, lib, config, inputs, ... }:

{
  packages = [ 
    pkgs.git
    pkgs.python314Packages.mitogen
    pkgs.prettier
  ];

  languages.ansible.enable = true;
}
